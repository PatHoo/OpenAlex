"""DID 稳健统计：学者层聚类稳健标准误 + 平行趋势联合 F 检验。

只用 Python 标准库（无 numpy/scipy），保证在没有科学计算环境也能复算。
实现内容：
- OLS：正规方程 + 高斯-约当消元（带部分主元）求 beta 与 (X'X)^-1；
- CR1 聚类稳健协方差：bread=(X'X)^-1，meat=Σ_g X_g'e_g e_g'X_g，
  小样本校正 G/(G-1) * (N-1)/(N-K)；
- Wald F 检验：H0: R·beta = 0，F = (Rb)'(R V R')^-1 (Rb)/q，分母自由度 G-1；
- F 分布尾概率：用不完全 beta 函数（Lentz 连分式）计算，避免依赖 scipy。

统计口径（与 wp14 的独立组-期近似严格区分）：
- 面板单元是学者-年，同一学者跨年相关；聚类到 scholar 层才是正确推断层级。
- treated 组只有 5 名学者 → G_treated=5，聚类 SE 在大样本下也偏乐观，必须标注小簇警示。
- 平行趋势联合检验：把 2014/2015/2016 三个 treated×year lead 系数联合检验为 0
  （baseline=2017），比逐一看 |t|<1.96 更严格。

本模块只做数值计算，不读库不联网；单元测试覆盖已知数值解。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


# ---------- 线性代数 ----------

def mat_inv(a: list[list[float]]) -> list[list[float]]:
    """高斯-约当消元求逆（部分主元）。奇异矩阵抛 ValueError。"""
    n = len(a)
    if n == 0 or any(len(row) != n for row in a):
        raise ValueError("mat_inv expects a square matrix")
    m = [list(row) + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError(f"singular matrix at column {col}")
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        m[col] = [v / pv for v in m[col]]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col]
            if f == 0.0:
                continue
            m[r] = [m[r][k] - f * m[col][k] for k in range(2 * n)]
    return [row[n:] for row in m]


def mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    if not a or not b or len(a[0]) != len(b):
        raise ValueError("mat_mul dimension mismatch")
    bt = list(zip(*b))
    return [[sum(x * y for x, y in zip(row, col)) for col in bt] for row in a]


def mat_vec(a: list[list[float]], v: list[float]) -> list[float]:
    return [sum(x * y for x, y in zip(row, v)) for row in a]


# ---------- F / t 分布尾概率（不完全 beta） ----------

def _betacf(a: float, b: float, x: float) -> float:
    """Lentz 连分式计算不完全 beta 的连续分数部分（Numerical Recipes 风格）。"""
    maxit, eps, fpmin = 300, 1e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    """正则化不完全 beta 函数 I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
              + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def f_sf(f: float, df1: int, df2: int) -> float:
    """F 分布上尾概率 P(F_df1,df2 > f)。"""
    if f <= 0:
        return 1.0
    z = df1 * f / (df1 * f + df2)
    return 1.0 - betainc_reg(df1 / 2.0, df2 / 2.0, z)


def t_sf_two(t: float, df: int) -> float:
    """t 分布双侧尾概率。"""
    if t == 0.0:
        return 1.0
    return betainc_reg(df / 2.0, 0.5, df / (df + t * t))


# ---------- OLS + 聚类稳健协方差 ----------

@dataclass
class OLSClusterResult:
    beta: list[float]
    se: list[float]
    vcov: list[list[float]]
    n: int
    k: int
    n_clusters: int
    r2: float

    def t_stat(self, idx: int) -> float:
        return self.beta[idx] / self.se[idx] if self.se[idx] > 0 and math.isfinite(self.se[idx]) else float("nan")

    def p_value(self, idx: int) -> float:
        t = self.t_stat(idx)
        return t_sf_two(t, self.n_clusters - 1) if math.isfinite(t) else float("nan")


def ols_cluster(X: list[list[float]], y: list[float],
                clusters: list[str]) -> OLSClusterResult:
    """OLS + CR1 学者层聚类稳健标准误。

    X 不需要包含截距列（调用方自行决定是否加）；y 与 clusters 长度须等于 n。
    """
    n, k = len(y), len(X[0])
    if len(X) != n or len(clusters) != n:
        raise ValueError("X/y/clusters length mismatch")
    if n <= k:
        raise ValueError("not enough observations for OLS")

    xtx = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)]
           for a in range(k)]
    xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    inv = mat_inv(xtx)
    beta = mat_vec(inv, xty)
    resid = [y[i] - sum(X[i][a] * beta[a] for a in range(k)) for i in range(n)]

    # 每个聚类的得分向量 s_g = X_g' e_g
    scores: dict[str, list[float]] = {}
    for i, g in enumerate(clusters):
        s = scores.setdefault(g, [0.0] * k)
        for a in range(k):
            s[a] += X[i][a] * resid[i]

    meat = [[0.0] * k for _ in range(k)]
    for s in scores.values():
        for a in range(k):
            for b in range(k):
                meat[a][b] += s[a] * s[b]

    n_clusters = len(scores)
    if n_clusters < 2:
        raise ValueError("need at least 2 clusters for clustered SE")
    corr = (n_clusters / (n_clusters - 1.0)) * ((n - 1.0) / (n - k))
    inv_meat = mat_mul(inv, meat)
    vcov = [[corr * v for v in row] for row in mat_mul(inv_meat, inv)]
    se = [math.sqrt(max(vcov[a][a], 0.0)) for a in range(k)]

    ybar = sum(y) / n
    sst = sum((v - ybar) ** 2 for v in y)
    sse = sum(e * e for e in resid)
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")

    return OLSClusterResult(beta, se, vcov, n, k, n_clusters, r2)


@dataclass
class WaldResult:
    f_stat: float
    df1: int
    df2: int
    p_value: float
    passed: bool          # p >= 0.05，即不能拒绝"系数联合为 0"
    degraded: bool = False  # 协方差退化时降级为逐项 t 检验的联合近似


def wald_test(res: OLSClusterResult, indices: list[int], alpha: float = 0.05) -> WaldResult:
    """联合检验 H0: beta[indices] = 0（Wald F，分母自由度 G-1）。

    当 R·V·R' 奇异（某 lead 在组内零方差，如二值结果全 0/1）时，无法构造 F；
    降级为：用每个受约束系数的 |t| 最大值近似，p 取最小单项 p（Bonferroni 式的
    保守联合界），并标记 degraded=True，绝不伪造 F 统计量。
    """
    q = len(indices)
    if q == 0:
        raise ValueError("empty restriction set")
    r_v_r = [[res.vcov[i][j] for j in indices] for i in indices]
    r_b = [res.beta[i] for i in indices]
    df2 = res.n_clusters - 1
    try:
        inv = mat_inv(r_v_r)
    except ValueError:
        ts = [res.t_stat(i) for i in indices if math.isfinite(res.t_stat(i))]
        ps = [res.p_value(i) for i in indices if math.isfinite(res.p_value(i))]
        stat = max((t * t for t in ts), default=float("nan"))
        p = min(min(ps) * q, 1.0) if ps else 1.0
        return WaldResult(stat, q, df2, p, p >= alpha, degraded=True)
    stat = sum(r_b[i] * sum(inv[i][j] * r_b[j] for j in range(q)) for i in range(q)) / q
    p = f_sf(stat, q, df2)
    return WaldResult(stat, q, df2, p, p >= alpha)
