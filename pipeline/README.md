# 全量跑批 Pipeline（WP0–WP8）

> 从 10.5 万名单到可分析的关系库：名单导入 → OpenAlex 抓取 → 作品落库 → 消歧定案 → 署名落库 → 机构/ROR → 机构回填。
> 截至 2026-11-22：**1,000 人试跑已全量抓完并完成落库**（见 [实机结果](#实机结果2026-11-22)）。

## 目录

- [API key 与额度](#api-key-与额度)
- [环境准备](#环境准备)
- [编号口径（勿改）](#编号口径勿改)
- [工作包总览](#工作包总览)
- [运行顺序](#运行顺序)
- [进度与额度自查](#进度与额度自查)
- [输出文件](#输出文件)
- [实机结果（2026-11-22）](#实机结果2026-11-22)
- [踩坑与设计边界](#踩坑与设计边界)
- [下一步](#下一步)

## API key 与额度

> **polite pool 已废除（2026-02 起）**：`mailto=` 参数被忽略，所有真实调用必须带免费 API key。

- **免费 key**：$1/天，**30 req/s**，支持 cursor 翻页；list 类请求（`/authors?search=`、`/works?filter=`）约 $0.0001/次，单实体查询免费。
- **匿名（无 key）**：与同 IP 共享 $0.10/天，翻页只能拿第一页，仅够极小样本验证。
- **额度按账户/邮箱计，不按 key**：同一邮箱再申请返回 `409 account_exists`。换邮箱 = 独立 $1/天（本项目用两把 key 轮换绕过单日上限）。
- 429 分两类：`daily_budget`（当日额度耗尽，`Retry-After` 指向次日重置）与速率限流。前者脚本会**立即暂停**（见下）。

**获取**：https://openalex.org/signup 注册 → https://openalex.org/settings/api-key 复制 → 邮箱点激活链接（否则 `403 key_not_activated`）。
也可代申请：`POST https://api.openalex.org/api-keys`，body `{"email","name","agent"}`，返回 key + 激活链接。

**设置**（key 只走环境变量，绝不写入代码/仓库）：

```bash
# WSL Bash
export OPENALEX_API_KEY='你的key'
```

## 环境准备

- **抓取（wp12）**：Windows Python 3.13 或 WSL venv 均可。WSL 用隔离环境（Debian PEP668 禁止系统 pip）：
  ```bash
  /usr/bin/python3 -m venv $HOME/.venvs/openalex
  $HOME/.venvs/openalex/bin/pip install requests openpyxl psycopg2-binary -i https://pypi.tuna.tsinghua.edu.cn/simple
  ```
- **落库（wp3–wp8）**：走 WSL 原生 PostgreSQL 17（端口 5432）。口令在 `~/.openalex_db_pass`（权限 600）。
  - wp3 用 `psycopg2` 直连；wp5–wp8 通过 `subprocess` 调 `psql`。均从环境变量 `PGPASSWORD` 取口令。
  - AGE 图容器另在端口 5433（仅图投影用，落库不涉及）。
- **Git Bash 调 WSL** 需加 `MSYS_NO_PATHCONV=1`，否则 `/mnt/c/...` 被错误转换。
- 所有阶段共用同一份 `pipeline/data/batch_001/`（Windows/WSL 通过 `/mnt/c` 挂载同一目录），断点通用。

## 编号口径（勿改，与已入库三人 POC 对齐）

- `source_row` = 数据序号（不含表头，从 1 起）
- `src_id` = Excel 实际行号（含表头，从 2 起）= `source_row + 1`
- 已验证：Becke 17/16、Perdew 18/17、Newman 29/28 与种子库完全一致；导入用 `ON CONFLICT` 幂等。

## 工作包总览

| 脚本 | 工作包 | 职责 | 结果 |
|---|---|---|---|
| `wp0_import.py` | WP0 | 读 105,026 行名单，生成 1,000 人子集与导入 SQL | source_scholar 子集 1,000 行 |
| `wp12_fetch.py` | WP1+WP2 | 作者检索候选 + 作品 cursor 翻页抓取（断点续跑、请求留痕） | 1,000 人 / 922,627 篇 |
| `wp3_import_works.py` | WP3 | 作品按 openalex_id 去重导入 work | 865,111 篇唯一作品 |
| `wp4_analyze_candidates.py` | WP4a | 候选信号分析/回测/冲突检测 | 信号分布报告 |
| `wp4_disambiguate.py` | WP4b | 消歧分级 A/B/HOLD/DUP | A710/B178/HOLD98/DUP11 |
| `wp4_load_decisions.py` | WP4c | 定案幂等落库 | scholar 896 / identity_map 1,700 |
| `wp5_import_authorship.py` | WP5 | 从 works.jsonl 定位目标署名建 authorship | 846,012 条署名 |
| `wp6_extract_institutions.py` | WP6 | 从 works.jsonl 抽机构/ROR/国家/别名 | 31,388 机构 / 181.6 万别名 |
| `wp7_load_institutions.py` | WP7 | 国家字典 + 种子机构补 ROR + 机构/别名入库 | country 203 / inst 31,388 |
| `wp8_backfill_institution.py` | WP8 | raw_affiliation 匹配别名回填 matched_institution_id | 646,069 条回填 |
| `export_requests_sql.py` | — | 请求留痕 JSONL → api_request 表 | 幂等导入 |

> **为什么 WP1/WP2 合在 `wp12_fetch.py`**：两阶段是同一遍循环里连续的两次 API 调用（对每个 src_id 先搜候选、拿到 author_id 立刻抓作品），共用客户端与断点。拆开会重复扫名单、重复管理状态。`checkpoint.json` 每个 src_id 同时记 `wp1`/`wp2`/`works_count`。从 WP3 起各步是独立的库操作，故一号一文件。

## 运行顺序

```bash
cd /mnt/c/Users/quith/Desktop/OpenAlex/pipeline   # WSL
VENV=$HOME/.venvs/openalex/bin/python

# ---------- 抓取 ----------
python wp0_import.py                                   # WP0：名单 + 1,000 人子集
# 3 人极小样本（无 key 可跑，只翻第一页）
OPENALEX_API_KEY=<key> $VENV -u wp12_fetch.py --limit 3 --stage all --max-works 5
# 1,000 人试跑（最大速度）
OPENALEX_API_KEY=<key> OA_RATE=30 $VENV -u wp12_fetch.py --limit 1000 --stage all

# ---------- 落库（顺序执行） ----------
PGPASSWORD=$(cat ~/.openalex_db_pass) $VENV -u wp3_import_works.py
$VENV wp4_analyze_candidates.py                        # 纯 Python，看信号报告
$VENV wp4_disambiguate.py                               # 生成 identity_decisions.jsonl（分级清单，可先审阅）
$VENV wp4_load_decisions.py --dry-run                   # 先看定案落库预览
$VENV wp4_load_decisions.py                             # 按确认口径落库
$VENV wp5_import_authorship.py
$VENV wp6_extract_institutions.py
$VENV wp7_load_institutions.py
$VENV wp8_backfill_institution.py --dry-run             # 先看覆盖率
$VENV wp8_backfill_institution.py                       # 正式回填
```

**断点续跑**：wp12 读 `checkpoint.json`、wp3 读 `import_progress.json`，已完成的自动跳过。额度耗尽时 wp12 捕获 `APIBudgetExhausted`，停在**当前学者**——不写半截作品、不标记完成，下次续跑补抓，不会静默丢数据。

**消歧口径**（用户 2026-11-22 确认）：严格 A 级 + 每组重复行只建 h17 最高主行 + A/B 级都建映射。HOLD（98 人）与 B 级（178 人）留待人工复核。

## 进度与额度自查

WSL 家目录已备两个脚本：

| 脚本 | 作用 |
|---|---|
| `~/oa_progress.sh` | 只读 checkpoint，秒回当前完成人数/断点位置 |
| `~/oa_quota.sh` | 查 `/rate-limit`，显示已用/剩余/重置倒计时（含明文 key，注意权限） |

```bash
bash ~/oa_progress.sh
bash ~/oa_quota.sh
```

> 不要用 `wc -l` 数 8.6G 的 `works.jsonl`——极慢，曾导致误以为卡住而 Ctrl+C。进度一律看 checkpoint。

## 输出文件

全部在 `pipeline/data/batch_001/`（已加入 `.gitignore`，不上传）：

| 文件 | 内容 |
|---|---|
| `source_scholar.jsonl` | 105,026 行原始名单（ISO3 国家码在 raw_payload） |
| `subset.jsonl` / `subset_source.sql` | 1,000 人试跑子集 / 幂等导入 SQL |
| `candidates.jsonl` | 每 src_id 的 top-5 作者候选（WP1，未自动定案） |
| `works.jsonl` | 每作品一行，含 authorships/institutions/署名串（WP2，8.6G） |
| `checkpoint.json` | 抓取断点（每 src_id 的 wp1/wp2/works_count） |
| `requests.jsonl` / `requests_import.sql` | 请求留痕（URL/时间/HTTP/sha256/字节）/ 导入 SQL |
| `identity_decisions.jsonl` | 消歧定案清单（WP4） |
| `import_progress.json` | 作品导入断点（WP3） |
| `_institutions.tsv` / `_aliases.tsv` / `_countries.txt` | 机构/ROR/国家/别名抽取结果（WP6） |

## 实机结果（2026-11-22）

**抓取**：1,000/1,000 学者完成（src_id 2–1001 连续无缺口），作品 **922,627 篇**；请求 10,760 次（10,759×200 + 1 条旧 key 遗留的 429）。两把 key 轮换，WSL `OA_RATE=30` 全程未触发速率限流。

**落库后数据库规模**（原生 PG17，`openalex` 库，约 1.36 GB）：

| 表 | 行数 | 说明 |
|---|---|---|
| `work` | 865,111 | 去重唯一作品 |
| `scholar` | 896 | 3 种子 + 893 新 |
| `identity_map` | 1,700 | openalex 896（A718/B178）+ orcid 804 |
| `name_variant` | 893 | — |
| `authorship` | 846,012 | 覆盖全部 896 学者；first/middle/last = 170,443/356,535/319,034 |
| `country` | 203 | — |
| `institution` | 31,388 | 全部带 ROR |
| `institution_alias` | 1,815,799 | 全 confirmed |
| authorship 已回填机构 | 646,069 | 占全部署名 76.37%、占有署名串的 95.85%；孤儿外键 0 |

## 踩坑与设计边界

- **COPY 必须用 `FORMAT csv`**：作品标题含换行、机构别名含 `\` 和反斜杠引号，`FORMAT text` 手工拼 `\t` 会导致列错位（`invalid input syntax`）。统一用 `csv.writer` + `COPY ... WITH (FORMAT csv)`。
- **别名内嵌 `\r\n`**：TSV 按 `\n` 分行会拆出坏行；抽取时把 `\r\n\t` 替换为空格并折叠空白。
- **API 细节**：`filter` 值用裸 ID（`A5109160404`），不要完整 URL；`select` 不能点选 `authorships.author.id` 等嵌套字段，只能整块取 `authorships`。
- **429 长睡 bug 已修**：旧逻辑对 `daily_budget` 的 429 直接 `sleep(Retry-After)`（约 22h）。现 `oa_client.py` 抛 `APIBudgetExhausted`，主循环立即干净暂停。
- **WP1 只产候选、不自动定案**：消歧口径需人工确认（本项目按严格 A 级执行）；HOLD/B 级待复核。
- **数据质量提示**：Druker（src 466）有 25,313 篇署名，源于 OpenAlex 该身份规模（抓取阶段即如此），非落库重复，属源数据同名合并问题。
- **磁盘**：`works.jsonl` 8.6G 是最大项；全量 105,027 人保守估 ~230G（精简后可读到 ~16G）。当前 C 盘/WSL 空间充足。

## 下一步

1. 按 `matched_institution_id` + `work.publication_year` 算**年度主机构/主国家**（众数，并列不强选）。
2. 跑 **C1–C5 事件引擎**与 **DID**（2017–2018 中美科技战）。
3. 可视化与 Agent 平台（见根目录 README「项目状态」）。
