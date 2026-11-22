# 全量跑批 Pipeline（WP0–WP14）

> 从 10.5 万名单到可分析的关系库：名单导入 → OpenAlex 抓取 → 作品落库 → 消歧定案 → 署名落库 → 机构/ROR → 机构回填 → 年度面板 → C1–C5 事件 → D/I/S 学者分类 → 中国背景候选队列 → 2018 DID。
> 截至 2026-11-22：**1,000 人试跑已全量抓完、落库，并跑通分析层（面板 + 事件 + 分类 + 队列 + DID）**（见 [实机结果](#实机结果2026-11-22)）。

## 目录

- [API key 与额度](#api-key-与额度)
- [环境准备](#环境准备)
- [编号口径（勿改）](#编号口径勿改)
- [工作包总览](#工作包总览)
- [运行顺序](#运行顺序)
- [回归测试](#回归测试)
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
| `wp9_build_panel.py` | WP9 | 年度众数主机构 + 并列 + 次级国家/机构 + 占比 | annual_panel 42,046 行 / share 96,481 行 |
| `wp10_event_engine.py` | WP10 | C1–C5 事件 + 三指标证据 + C2 抑制 + C4 三态 | 13,355 事件 / 53,425 证据行 |
| `wp11_classify.py` | WP11 | 按 8.5 算 D/I/S → M1–M8 分类 + 三类次数（独立视图） | 896 人分类，631 可定类 / 265 不确定 |
| `wp13_cohort_screen.py` | WP13 | 拼音姓氏 + 中国内地/中国香港/中国台湾机构署名 + 2017 在美 → 候选队列 | cohort_member 111（7 high / 104 low）/ 证据 819 行 |
| `wp14_did.py` | WP14 | treated=high、control=未入队、post=2018，2x2 + 事件研究 | did_panel 3,375 / 估计 4 / 逐年效应 28 |
| `design/class_rule_85_seed.sql` | — | 8.5.2 权威 D/I/S→M1..M8 八类映射（原 schema 仅 M8 一条） | class_rule 8 条全覆盖 |
| `design/cohort_did_seed.sql` | — | 队列证据与 DID 表增量迁移（可重复执行） | +3 表，did_panel +4 列 |
| `dbutil.py` | — | psql 调用封装（检查退出码，不用 `\| tail` 吞错误） | 供 wp9–wp14 复用 |
| `tests/test_pipeline_safety.py` | — | P0 静默丢数回归测试（7 项） | 全通过 |
| `tests/test_wp11_classification.py` | — | 8.5 分类口径回归测试（6 项，静态） | 全通过 |
| `tests/test_cohort_did.py` | — | 队列与 DID 口径回归测试（14 项，静态） | 全通过 |
| `export_requests_sql.py` | — | 请求留痕 JSONL → api_request 表 | 幂等导入 |

> **为什么 WP1/WP2 合在 `wp12_fetch.py`**：两阶段是同一遍循环里连续的两次 API 调用（对每个 src_id 先搜候选、拿到 author_id 立刻抓作品），共用客户端与断点。拆开会重复扫名单、重复管理状态。`checkpoint.json` 每个 src_id 同时记 `wp1`/`wp2`/`works_count`。从 WP3 起各步是独立的库操作，故一号一文件。
>
> **为什么队列是 WP13、DID 是 WP14**：`wp12_fetch.py` 已被抓取阶段占用；队列筛选依赖 WP9 面板与 WP11 分类之后的证据地基，DID 又依赖队列，故顺延为 WP13/WP14，不改动既有抓取断点脚本名。

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

# ---------- 分析层（在已落库地基上跑，可反复重跑） ----------
$VENV wp9_build_panel.py --dry-run                      # 统计有效署名/学者年
$VENV wp9_build_panel.py                                # 建 annual_panel + affiliation_share
$VENV wp10_event_engine.py --dry-run                    # 统计候选事件
$VENV wp10_event_engine.py                              # 建事件 + 证据（rule_version=A_1000_v1）
PGPASSWORD=$(cat ~/.openalex_db_pass) psql -h 127.0.0.1 -U postgres -d openalex -v ON_ERROR_STOP=1 -f ../design/class_rule_85_seed.sql   # 补 8.5.2 八类映射
$VENV wp11_classify.py --dry-run                        # 预估 D/I/S 组合分布
$VENV wp11_classify.py                                  # 建 mobility_class + v_mobility_counts

# ---------- 队列与 DID（先做一次增量迁移，可重复执行） ----------
PGPASSWORD=$(cat ~/.openalex_db_pass) psql -h 127.0.0.1 -U postgres -d openalex -v ON_ERROR_STOP=1 -f ../design/cohort_did_seed.sql
$VENV wp13_cohort_screen.py --dry-run                    # 预估 high/medium/low，不写库
$VENV wp13_cohort_screen.py                              # 建 cohort_member + cohort_evidence
$VENV wp14_did.py --dry-run                              # 看 treated/control 与年份覆盖
$VENV wp14_did.py                                        # 建 did_panel/did_estimate/did_annual_effect + 导出 CSV
```

**断点续跑与失败安全**：wp12 读 `checkpoint.json`、wp3 读 `import_progress.json`，已完成的自动跳过。请求失败（额度耗尽 / 网络重试耗尽 / 非限流 4xx / key 未激活）时 wp12 抛 `APIFetchFailed` 或 `APIBudgetExhausted`，停在**当前学者**——不写半截作品、不写空候选、不标记完成，下次续跑补抓。wp5 只有 COPY **成功后**才推进 offset 并清空 staging；失败时保留现场，同参重跑即可补批。

## 回归测试

```bash
cd /mnt/c/Users/quith/Desktop/OpenAlex/pipeline
python -m unittest discover -s tests -v       # 27 项，不访问 API/数据库
```

锁定两条 P0 不变量：①请求失败不得标记学者完成、不得写入空候选/半截作品；②COPY 失败不得推进 offset、不得清空 staging，成功后才推进。dry-run 也不能污染正式 staging。另锁定 8.5 分类口径：`class_rule` seed 必须覆盖 D/I/S 全 8 组合、D/I 只认 Confirmed/Probable 未抑制事件、阴性维度有 Possible 未决候选时 formal_class 必须留空、次数走独立视图。WP13/WP14 测试锁定：拼音 token 边界、high 必须 2017 非并列在美、算法不得断言出生国、人工复核行不被覆盖；DID treated 只取 high、post=2018、事件只取 A_1000_v1 的 Confirmed/Probable 未抑制、平行趋势与独立组-期标准误必须写明。

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
| `did_panel.csv` / `did_annual_effect.csv` | WP14 导出的 DID 面板与逐年效应（供 pandas/statsmodels 做聚类稳健推断） |

## 实机结果（2026-11-22）

**抓取**：1,000/1,000 学者完成（src_id 2–1001 连续无缺口），作品 **922,627 篇**；请求 10,760 次（10,759×200 + 1 条旧 key 遗留的 429）。两把 key 轮换，WSL `OA_RATE=30` 全程未触发速率限流。

**落库后数据库规模**（原生 PG17，`openalex` 库，约 2.1 GB；地基约 1.36 GB + 分析层）：

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

**分析层（2026-11-22 新跑，`A_1000_v1`）**：

| 表 | 行数 | 说明 |
|---|---|---|
| `annual_panel` | 42,046（wp9 新建） | 覆盖 896 学者，年份 1869–2026；并列年 1,890、有次级国家 9,882 |
| `affiliation_share` | 96,481 | 主机构标记 44,284 行，share 合计按学者年归一 |
| `mobility_event` | 13,355 | 覆盖 895/896 学者；C1 3,644 / C2 996 / C3 8,504 / C4 211 |
| `event_evidence` | 53,425 | 四判据各 13,355 行 + external_cv 5 行；孤儿外键 0 |
| `class_rule` | 8 | 8.5.2 权威 D/I/S→M1..M8 全组合映射（来自原始 Word，非臆造） |
| `mobility_class` | 896（`A_1000_v1`） | 631 可正式定类 / 265 标不确定；0 映射不一致、0 孤儿 |

四判据通过率：min3works 11,948、share_threshold 9,835、tie_policy 12,765、two_year_dominant 12,459（各除以 13,355）。证据等级：Probable 8,477 / Possible 4,873 / Confirmed 5。C4 三态：confirmed 45 / not_sustained 149 / pending 17。

**中国背景候选队列（`CN_US_2017_v1`，2026-11-22 新跑）**：

| 表 | 行数 | 说明 |
|---|---|---|
| `cohort_member` | 111 | 7 high / 104 low；`birth_country` 全 NULL，未断言中国出生 |
| `cohort_evidence` | 819 | 拼音、中国内地/中国香港/中国台湾机构署名、2017 在美逐信号与权重 |

7 名 high：Wang, Joseph(114)、Hu, Frank B.(121)、Zhu, Jian-Kang(266)、Yang, Peidong(326)、Liu, Jie(358)、Peng, Fang Z.(802)、Sun, Shouheng(840)。均为强拼音姓氏 + 中国机构署名轨迹 + 2017 年美国非并列主机构。104 名 low 多为仅有中国合作署名但无拼音姓名信号的在美学者，仅供人工复核；`reviewed_by` 非空的行不会被后续重跑覆盖。

**DID（`DID_2018_A1000_v1`，2026-11-22 新跑）**：`did_panel` 3,375 组-年（treated 56 / control 3,319，7 vs 428 名学者，2014–2021）；`did_estimate` 4 行，`did_annual_effect` 28 行。treated=7 名 high 候选，control=428 名同期在美且未进队列者（medium/low 候选不进任一组）。2x2（前 2014–2017、后 2018–2021）结果：

| 结果变量 | DID 系数 | 标准误 | t | 平行趋势检查 |
|---|---:|---:|---:|---|
| `moves_any`（C1+C2 有效事件） | 0.112235 | 0.059855 | 1.875 | 通过（2014–2016 |t|<1.96） |
| `moves_intl`（C2） | 0.036167 | 0.035770 | 1.011 | 通过 |
| `cn_link`（当年中国主/次级署名） | -0.071429 | 0.130047 | -0.549 | 通过 |
| `log_works`（log(1+有效论文数)） | -0.107696 | 0.205049 | -0.525 | 通过 |

标准误为四个组-期单元样本方差的独立近似，未处理同一学者多年重复观测；正式推断需用导出的 `did_panel.csv` 做学者层聚类稳健回归。所有系数均不显著，且 treated 仅 7 人，**这是流程验证，不构成"科技战改变了在美中国学者流动"的实证结论**。

**分类分布（`A_1000_v1`，observed / 其中可正式定类）**：M1 6/1、M2 21/20、M4 160/47、M5 2/2、M6 467/361、M7 87/47、M8 153/153；M3（纯国际迁移）0 人。跨国迁移型（M3/M5/M7/M8）242 人、非跨国迁移型 654 人。三类次数与类型分开（视图 `v_mobility_counts`）：C1 通过验证 1,571 次、C2 429 次、C3 新增次级国家去对 5,477、C4 持续联系 211 次。

> **边界**：13,350 条事件 `actual_year` 为空、`year_source=signature_proxy`——署名年只是任职代理年，不得当真实迁移年。五条 Confirmed 全部来自 3 人小样学者（src 18/29）已核验的 `career_fact`，不是 1000 人新证据。C2 抑制 C1 为 0 行，因为事件视图的 CASE 对同一学者年只产出一个类型，C2 已在候选阶段优先。C3 占事件 64%，其"新增次级国家"多为国际合作署名代理，不等同于真实跨国流动。
>
> **`class_rule` 八类映射的来源纠正**：此前判断"方案 8.5 全表未提供、不能定类"是只看了设计文档的结论，**不完整**。原始 Word `reference/科学家空间流动数据库建立操作1030.docx` 8.5.2 节有完整八类 D/I/S 组合表、8.5.3 有分类示例、8.6.2 有次数统计规则。已据此补全 `class_rule` 并落地分类（见 `design/class_rule_85_seed.sql`）。但仍守两条边界：①D/I 只认 Confirmed/Probable，Possible 不计入 1；②阴性维度存在 Possible 未决候选时，`formal_class` 留空、`is_classified=false`（265 人），不把"未识别"当成"确定无流动"。三人小样复现时出现一处分歧：Perdew 1974 年 US→CA 有一条 Probable C2，自动口径记 `I=1`→M8，而原小样人工核验把该次判为访问、记 `I=0`→M6——这是自动署名口径与人工履历口径的固有差异，已如实保留未强行对齐。中国出生队列仍无外部证据：WP13 的 high 只是"中国背景代理"，WP14 的 DID 亦仅为 1000 人流程验证。

## 踩坑与设计边界

- **COPY 必须用 `FORMAT csv`**：作品标题含换行、机构别名含 `\` 和反斜杠引号，`FORMAT text` 手工拼 `\t` 会导致列错位（`invalid input syntax`）。统一用 `csv.writer` + `COPY ... WITH (FORMAT csv)`。
- **别名内嵌 `\r\n`**：TSV 按 `\n` 分行会拆出坏行；抽取时把 `\r\n\t` 替换为空格并折叠空白。
- **API 细节**：`filter` 值用裸 ID（`A5109160404`），不要完整 URL；`select` 不能点选 `authorships.author.id` 等嵌套字段，只能整块取 `authorships`。
- **429 长睡 bug 已修**：旧逻辑对 `daily_budget` 的 429 直接 `sleep(Retry-After)`（约 22h）。现 `oa_client.py` 抛 `APIBudgetExhausted`，主循环立即干净暂停。
- **WP1 只产候选、不自动定案**：消歧口径需人工确认（本项目按严格 A 级执行）；HOLD/B 级待复核。
- **数据质量提示**：Druker（src 466）有 25,313 篇署名，源于 OpenAlex 该身份规模（抓取阶段即如此），非落库重复，属源数据同名合并问题。
- **磁盘**：`works.jsonl` 8.6G 是最大项；全量 105,027 人保守估 ~230G（精简后可读到 ~16G）。当前 C 盘/WSL 空间充足。
- **`work.is_preprint` 不可用**：wp3 导入时该布尔列全为 false，但 `work_type='preprint'` 有 7,578 篇。wp9 的有效署名过滤以 `work_type IS DISTINCT FROM 'preprint'` 为准。
- **三指标判据 1 的口径修正**：设计文档 3.3 示例把 `two_year_dominant` 的 passed 写成两年主机构**相等**，与"事件=主机构变化"矛盾。wp10 按文字意图实现为"两年主机构均非并列（dominant）"，机构是否变化交给事件类型表达。
- **psql 调用禁止 `| tail`**：管道退出码是 `tail` 的，会把数据库错误吞成成功。统一走 `dbutil.run_psql`，直接检查 `returncode`。

## 下一步

1. **中国出生外部证据**：WP13 只做候选队列，`birth_country` 全 NULL。需 ORCID/CV/机构履历等外部来源人工复核 `cohort_member.reviewed_by`/`reviewed_at`，才能把 7 名 high 代理升级为"已证实中国出生"。
2. **正式 DID 推断**：1000 人 treated=7，只够跑通流程。全量后用 `did_panel.csv` 在 statsmodels/linearmodels 做学者层聚类稳健 SE，并补联合平行趋势 F 检验、匹配/权重等稳健性。
3. **265 人分类不确定复核**：阴性维度有 Possible 未决候选者 `formal_class` 留空，需人工核验候选事件后定类；Perdew 式"自动 I=1 vs 人工 I=0"的分歧需统一口径。
4. **可视化与 Agent 平台**：仓库当前无 app/web/vis 源码；国内合规底图供应商尚未选型（不能用 Leaflet+OSM 直接上线）。
5. **AGE 图投影**：1000 人数据尚未投影；目标 7 类节点/14 种边，现有小样仅 5 类/9 种。
6. **全量 105,027 人**：地基与分析层脚本已就绪，但受 API 额度（$1/天/key）与磁盘（works.jsonl 全量估 ~230G）约束，尚未启动。
