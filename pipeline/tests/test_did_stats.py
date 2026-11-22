"""DID 稳健统计单元测试：不联网、不连库，验证数值正确性。

用可解析验算的小样本锁定：
- mat_inv 逆矩阵还原单位阵；
- OLS 在精确线性关系上恢复系数；
- 聚类稳健 SE 对同一学者重复观测与独立观测的差异；
- F 分布尾概率与已知临界值一致（F(1,∞) 在 3.84 处约 0.05）；
- Wald 检验在明确非零系数上拒绝、在零系数上不拒绝；
- 奇异 RVR 时降级不抛异常且标记 degraded。
"""
from __future__ import annotations

import unittest

from did_stats import (
    OLSClusterResult,
    betainc_reg,
    f_sf,
    mat_inv,
    mat_mul,
    ols_cluster,
    t_sf_two,
    wald_test,
)


class LinearAlgebraTest(unittest.TestCase):
    def test_mat_inv_roundtrip(self) -> None:
        a = [[4.0, 2.0], [1.0, 3.0]]
        prod = mat_mul(a, mat_inv(a))
        for i in range(2):
            for j in range(2):
                self.assertAlmostEqual(prod[i][j], 1.0 if i == j else 0.0, places=10)

    def test_mat_inv_singular_raises(self) -> None:
        with self.assertRaises(ValueError):
            mat_inv([[1.0, 2.0], [2.0, 4.0]])


class DistributionTest(unittest.TestCase):
    def test_betainc_edges(self) -> None:
        self.assertEqual(betainc_reg(2.0, 3.0, 0.0), 0.0)
        self.assertEqual(betainc_reg(2.0, 3.0, 1.0), 1.0)
        self.assertAlmostEqual(betainc_reg(1.0, 1.0, 0.4), 0.4, places=10)

    def test_f_tail_known_critical_value(self) -> None:
        # F(1, 100000) 的 5% 临界值约 3.841
        self.assertAlmostEqual(f_sf(3.841, 1, 100000), 0.05, places=2)
        self.assertLess(f_sf(10.0, 1, 100000), 0.01)
        self.assertGreater(f_sf(0.5, 1, 100000), 0.3)

    def test_t_tail_known_critical_value(self) -> None:
        # 正态近似：双侧 1.96 对应 p≈0.05
        self.assertAlmostEqual(t_sf_two(1.96, 100000), 0.05, places=2)
        self.assertAlmostEqual(t_sf_two(0.0, 10), 1.0, places=10)


class OLSTest(unittest.TestCase):
    def test_recovers_exact_linear_coefficients(self) -> None:
        X = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0], [1.0, 3.0]]
        y = [5.0, 7.0, 9.0, 11.0]  # y = 5 + 2x
        res = ols_cluster(X, y, ["a", "b", "c", "d"])
        self.assertAlmostEqual(res.beta[0], 5.0, places=9)
        self.assertAlmostEqual(res.beta[1], 2.0, places=9)
        self.assertAlmostEqual(res.r2, 1.0, places=9)

    def test_cluster_se_matches_hand_computed_sandwich(self) -> None:
        """聚类 SE 直接按 CR1 sandwich 手算核对，不依赖"必须更大"的构造性质。"""
        X = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0], [1.0, 0.0], [1.0, 1.0], [1.0, 3.0]]
        y = [1.0, 3.0, 4.0, 2.0, 2.0, 7.0]
        cl = ["a", "a", "a", "b", "b", "b"]
        res = ols_cluster(X, y, cl)
        # 手算：X'X 与 (X'X)^-1
        xtx = [[sum(row[a] * row[b] for row in X) for b in range(2)] for a in range(2)]
        inv = mat_inv(xtx)
        # OLS beta
        xty = [sum(X[i][a] * y[i] for i in range(6)) for a in range(2)]
        beta = [sum(inv[a][b] * xty[b] for b in range(2)) for a in range(2)]
        # 每簇得分 s_g = X_g'e_g
        meat = [[0.0] * 2 for _ in range(2)]
        for g in set(cl):
            s = [0.0, 0.0]
            for i in range(6):
                if cl[i] != g:
                    continue
                e = y[i] - sum(X[i][a] * beta[a] for a in range(2))
                for a in range(2):
                    s[a] += X[i][a] * e
            for a in range(2):
                for b in range(2):
                    meat[a][b] += s[a] * s[b]
        corr = (2 / 1) * (5 / 4)  # G/(G-1)*(N-1)/(N-K)
        v11 = corr * sum(inv[1][c] * meat[c][d] * inv[d][1]
                         for c in range(2) for d in range(2))
        self.assertAlmostEqual(res.se[1], v11 ** 0.5, places=9)

    def test_cluster_vs_independent_se_differ(self) -> None:
        """同数据下聚类分组与全独立分组的 SE 应不同（推断层级确实改变）。"""
        X = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0], [1.0, 0.0], [1.0, 1.0], [1.0, 3.0]]
        y = [1.0, 3.0, 4.0, 2.0, 2.0, 7.0]
        res_cluster = ols_cluster(X, y, ["a", "a", "a", "b", "b", "b"])
        res_indep = ols_cluster(X, y, ["a", "b", "c", "d", "e", "f"])
        self.assertNotAlmostEqual(res_cluster.se[1], res_indep.se[1], places=6)

    def test_zero_se_gives_nan_not_crash(self) -> None:
        res = OLSClusterResult(
            beta=[1.0, 0.0], se=[1.0, 0.0], vcov=[[1.0, 0.0], [0.0, 0.0]],
            n=10, k=2, n_clusters=5, r2=0.0,
        )
        import math
        self.assertTrue(math.isnan(res.t_stat(1)))
        self.assertTrue(math.isnan(res.p_value(1)))

    def test_requires_at_least_two_clusters(self) -> None:
        X = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]]
        with self.assertRaises(ValueError):
            ols_cluster(X, [1.0, 2.0, 3.0], ["only", "only", "only"])


class WaldTestTest(unittest.TestCase):
    def _result(self) -> OLSClusterResult:
        X = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0], [1.0, 3.0], [1.0, 4.0], [1.0, 5.0]]
        y = [1.0, 1.1, 0.9, 1.0, 1.2, 0.8]  # 斜率≈0
        return ols_cluster(X, y, ["a", "b", "c", "d", "e", "f"])

    def test_zero_coefficient_not_rejected(self) -> None:
        w = wald_test(self._result(), [1])
        self.assertTrue(w.passed)
        self.assertGreater(w.p_value, 0.05)

    def test_large_coefficient_rejected(self) -> None:
        X = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0], [1.0, 3.0], [1.0, 4.0], [1.0, 5.0]]
        y = [1.0, 4.0, 7.0, 10.0, 13.0, 16.0]
        res = ols_cluster(X, y, ["a", "b", "c", "d", "e", "f"])
        w = wald_test(res, [1])
        self.assertFalse(w.passed)
        self.assertLess(w.p_value, 0.05)

    def test_degraded_on_singular_covariance(self) -> None:
        res = OLSClusterResult(
            beta=[0.0, 0.0], se=[1.0, 0.0],
            vcov=[[1.0, 0.0], [0.0, 0.0]],
            n=10, k=2, n_clusters=5, r2=0.0,
        )
        w = wald_test(res, [1])
        self.assertTrue(w.degraded)
        self.assertGreaterEqual(w.p_value, 0.0)
        self.assertLessEqual(w.p_value, 1.0)


if __name__ == "__main__":
    unittest.main()
