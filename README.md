# OpenAlex 科学家空间流动数据库

**全球高被引学者的机构隶属与空间流动轨迹重建** — 数据基建 · 政策研究 · AI 科研工具平台

> **项目状态**：第一期方法可行性验证（Proof of Concept）已完成；1,000 人试跑已全量抓取并完成落库（见 [项目状态](#项目状态)）。全量 10.5 万学者跑批、DID 分析与 Agent 平台尚未实施。

## 目录

- [项目背景](#项目背景)
- [核心方法](#核心方法)
- [目录结构](#目录结构)
- [数据资产](#数据资产)
- [技术架构](#技术架构)
- [快速开始](#快速开始)
- [项目状态](#项目状态)
- [技术栈](#技术栈)

## 项目背景

这是一个甲方委托、分四期付款的研究项目。终点不只是建库，而是一个科研政策问题：

1. 建立全球高被引科学家的历年机构迁移轨迹库；
2. 从中筛出「中国出生、现居美国」的学者；
3. 用双重差分（DID）检验 2017–2018 中美科技战对在美学者流动的影响；
4. 把整套能力做成科研 Agent 平台：AI 统计、地图/热力/流线可视化、调度本地 SPSS/Stata/ArcGIS、文献管理、论文写作，最终本地化部署交付。

数据源以 **OpenAlex** 为核心，辅以 **ROR**（机构标准化）、**ORCID**（身份消歧）、**Crossref**（履历佐证）。原始样本来自 Elsevier 2017 高被引名单，共 **105,027** 名学者。

## 核心方法

技术内核是一套 **C1–C5 事件引擎**，专门解决「论文署名变化 ≠ 真实职业迁移」这个难题：

| 事件 | 含义 | 处理 |
|------|------|------|
| **C1** | 国内换机构 | 机构变、国家不变 |
| **C2** | 跨国迁移 | 优先级高于 C1，同年 C1 被抑制，避免重复计数 |
| **C3** | 新增次级国家 | 兼职/多重隶属 |
| **C4** | 原主国降级为次级并持续 | pending → confirmed / not_sustained 三态 |
| **C5** | 机构改名但 ID 不变 | 排除伪迁移，不生成事件 |

主机构与主国家**分别独立按年度众数计算**，允许并列不强选；明确禁止用 LOCF 填充年充当事件证据。候选事件需过三道自动验证（连续两年主导、署名占比 ≥ 60%、累计 ≥ 3 篇），再查官方 CV / ORCID 履历，允许论文年与履历年 ±2 年偏差并以履历为准，最终授予 Confirmed / Probable / Possible / Rejected 证据等级。汇总为 **D/I/S 三维 → M1–M8 八类学者**。

A/B 双轨设计：A 为基线（60% 阈值），B 为敏感性实验（50%/70%），**独立重算、不覆盖 A**。

## 目录结构

```
OpenAlex/
├── reference/                        # 甲方原始资料与样本数据（只读参考）
│   ├── （需求清单）.doc
│   ├── 科学家空间流动数据库建立操作1030.docx
│   ├── Table-S1-career-2017.xlsx
│   ├── 1030版_新三学者基线与增量对照数据.xlsx
│   ├── 1030版测试报告.pdf
│   ├── 1030版满足度说明.pdf
│   └── 1030版逐人轨迹核查报告.pdf
├── design/                           # 数据库设计与原型部署
│   ├── 关系库+AGE_图模式设计.md
│   ├── schema.sql
│   ├── 三人小样_seed.sql
│   ├── 三人小样_AGE投影.sql
│   ├── verify_poc.sql                    # 导入后可执行的一致性/完整性核对
│   ├── 三人小样原型_部署文档.md
│   └── 三人小样原型_部署文档_Docker_WSL版.md
├── pipeline/                         # 全量跑批、落库与分析层（WP0–WP11）
│   ├── README.md                         # key/额度、环境、工作包总览、运行顺序、实机结果
│   ├── config.py                         # 环境变量/路径/限速
│   ├── oa_client.py                      # 认证、限速、429 额度保护、请求留痕
│   ├── wp0_import.py                     # WP0：105,026 人名单 + 1,000 人子集
│   ├── wp12_fetch.py                     # WP1+WP2：作者检索 + 作品抓取（断点续跑）
│   ├── wp3_import_works.py               # WP3：作品去重导入 work（865,111 篇）
│   ├── wp4_analyze_candidates.py         # WP4a：候选信号分析
│   ├── wp4_disambiguate.py               # WP4b：消歧分级 A/B/HOLD/DUP
│   ├── wp4_load_decisions.py             # WP4c：定案幂等落库
│   ├── wp5_import_authorship.py          # WP5：署名落库（846,012 条）
│   ├── wp6_extract_institutions.py       # WP6：从 works 抽机构/ROR/别名
│   ├── wp7_load_institutions.py          # WP7：国家/机构/ROR 入库
│   ├── wp8_backfill_institution.py       # WP8：回填 matched_institution_id
│   ├── wp9_build_panel.py                # WP9：年度面板 + 机构占比
│   ├── wp10_event_engine.py              # WP10：C1–C5 事件 + 三指标证据
│   ├── wp11_classify.py                  # WP11：D/I/S → M1–M8 分类 + 三类次数视图
│   ├── dbutil.py                         # psql 封装（检查退出码）
│   ├── tests/test_pipeline_safety.py     # P0 静默丢数回归测试（7 项）
│   ├── tests/test_wp11_classification.py # 8.5 分类口径回归测试（6 项）
│   ├── export_requests_sql.py            # 请求留痕导入数据库
│   └── data/batch_001/                   # 试跑产物（gitignored，不上传）
└── output/                           # 分析与交付物
    └── openalex_audit_20261121/
        ├── sources/materials_digest.md
        ├── stage1/organized_draft.md
        ├── stage2/formatted-OpenAlex验收质疑清单.html
        └── stage3/OpenAlex科学家流动数据库_验收质疑清单.docx
```

## 数据资产

| 文件 | 内容 |
|------|------|
| `Table-S1-career-2017.xlsx` | Elsevier 2017 高被引名单：105,027 行 × 42 列，含排名、引用、h 指数、学科等；Key 表为 43 字段说明 |
| `1030版_新三学者基线与增量对照数据.xlsx` | 25 个工作表，仅对 3 名学者跑通全链路；含身份映射、年度面板、事件审计、证据卡、请求日志与逐人轨迹矩阵 |
| 三份 1030 版 PDF 报告 | 测试报告、满足度说明、逐人轨迹核查报告；结论均带「结果与证据边界」声明 |

## 技术架构

**关系库为唯一真相源，AGE 图是可丢弃的派生投影**：

- **PostgreSQL + PostGIS**：22 张表覆盖身份、作品署名、机构地理、年度面板、事件证据分类、请求日志。同一 schema 已导入两处——AGE 图容器（PG16）与原生落库（PG17:5432，承载 1,000 人试跑）。`rule_version` 进入事件/分类表唯一约束，A 轨以触发器锁定不可变。
- **Apache AGE**：目标模式为 7 类节点 / 14 种边；三人小样投影实际实现 5 类节点 / 9 种边（暂不含 `Work`/`Alias` 节点及 `AUTHORED`/`SIGNED_AT`/`RENAMED_TO`/`IN_COHORT` 边）。时间作为 `AFFILIATED` 边属性、规则版本作版本属性。图随时可重建，不影响原始数据。
- **分工**：统计判定（众数、占比、C2 抑制、阈值敏感性）全在 SQL；迁移轨迹、改名链、合作网络等路径查询走图；DID 回归导出到 pandas/statsmodels。

## 快速开始

三人小样原型已备好可执行的建表、种子与图投影脚本。Docker + WSL 环境：

```bash
# 旧 tag PG16_latest 已失效；直连 Docker Hub 超时可用镜像站
docker pull docker.1ms.run/apache/age:release_PG16_1.6.0
docker tag docker.1ms.run/apache/age:release_PG16_1.6.0 apache/age:release_PG16_1.6.0

export OPENALEX_DB_PASSWORD='<请设置强密码>'
docker run -d --name openalex-pg -e POSTGRES_PASSWORD="$OPENALEX_DB_PASSWORD" \
  -e POSTGRES_DB=openalex -p 5433:5432 apache/age:release_PG16_1.6.0

# AGE 官方镜像不含 PostGIS；schema.sql 用到 GEOGRAPHY，需补装兼容版本
docker exec openalex-pg bash -lc 'sed -i "s|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g; s|http://deb.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g" /etc/apt/sources.list.d/debian.sources && apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16-postgis-3=3.6.4+dfsg-2.pgdg13+1 postgresql-16-postgis-3-scripts=3.6.4+dfsg-2.pgdg13+1'
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -c 'CREATE EXTENSION IF NOT EXISTS age; CREATE EXTENSION IF NOT EXISTS postgis;'

docker cp design/schema.sql               openalex-pg:/tmp/schema.sql
docker cp design/三人小样_seed.sql          openalex-pg:/tmp/seed.sql
docker cp design/三人小样_AGE投影.sql        openalex-pg:/tmp/graph.sql
docker cp design/verify_poc.sql            openalex-pg:/tmp/verify.sql
docker cp design/api_request_seed.sql      openalex-pg:/tmp/api_seed.sql
docker cp design/api_request_crossref_seed.sql openalex-pg:/tmp/crossref_seed.sql
docker cp design/event_evidence_seed.sql  openalex-pg:/tmp/ee_seed.sql
docker cp design/rule_test_seed.sql       openalex-pg:/tmp/rt_seed.sql

docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/schema.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/seed.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/graph.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/api_seed.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/crossref_seed.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/ee_seed.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/rt_seed.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -v ON_ERROR_STOP=1 -f /tmp/verify.sql
```

导入后种子数据为：3 条原始名单行 / 3 学者 / 11 机构 / 139 年度面板 / 126 署名占比 / 6 条迁移事件 / 4 条外部履历 / 9 条分类结果 / 30 条事件证据 / 19 项规则测试 / 220 条 API 请求日志（64 基线 + 156 Crossref 补查）。跑 `三人小样原型_部署文档_Docker_WSL版.md` 第 7.1 节的 V1–V5 图查询，应看到 Newman 的完整轨迹与三人分类现状。

## 项目状态

**已完成**：三人小样的全链路验证（身份匹配 → 抓取留痕 → 机构标准化 → 年度面板 → C1–C5 事件 → 履历佐证 → 轨迹矩阵）。原始 Excel 中的留痕数据现已全部迁入 SQL：`api_request` 220 条（64 基线 + 156 Crossref 补查）、`event_evidence` 30 条（6 事件 × 5 判据）、`rule_test` 19 项（全部 passed）。三张留痕表不再是空表。

**未完成 / 边界**：

- 验证样本为 Becke、Perdew、Newman 三人，占 10.5 万目标学者约 **0.003%**，属 Proof of Concept 而非全量成果。
- Newman 证据完整、判为 M8；Becke / Perdew 因任职年份缺失与并列年，`mobility_class` 正式字段留空。
- 6 条有效迁移中，2 条用署名年代理、无真实任职年份；70% 阈值下部分事件从 Probable 降为 Possible。
- 「中国出生、现居美国」人群筛选、DID 分析、文献模块、论文写作、本地化部署尚未实施。
- 全量跑批尚未开始（目标 105,027 人）；但 **1,000 人试跑已全部完成落库并跑通分析层**（详见 `pipeline/README.md`）：抓取 922,627 篇作品（请求 10,760 次），去重入库 865,111 篇 work；消歧定案 scholar=896 / identity_map=1,700；署名 846,012 条；机构 31,388（全带 ROR）+ 别名 181.6 万；已回填 646,069 条署名的 `matched_institution_id`。分析层已建 42,046 个学者年面板、13,355 个 C1–C5 事件，并按原始方案 8.5 完成 D/I/S→M1–M8 分类（896 人，631 可正式定类 / 265 标不确定）。但 **中国学者队列、DID 未做**。数据库约 2.1 GB。
- 消歧按严格 A 级执行，HOLD（98 人）与 B 级（178 人）待人工复核；这些数字属小样试跑，非全量成果。
- 未做用户人工抽样审计；报告作者主动声明「只完成小样本，不可宣称全方案通过」。

因此本仓库的材料定位为**可审计的小样施工成果 + 证据边界声明**，而非验收通过报告。`output/` 中的验收质疑清单据此整理了付款前需澄清的事项。

> **2026-11-21 修复记录**：补齐 `source_scholar` 种子以解除外键阻塞；修正 C5 消解 SQL 的列名、证据枚举与主键描述；给图脚本加上可重复执行保护；清理种子中的履历来源类型与 URL 污染；移除硬编码口令和他人绝对路径；新增 `design/verify_poc.sql` 供导入后逐项核对。详见 `design/关系库+AGE_图模式设计.md` 的版本说明。

### 本机 WSL 环境（已安装并验证）

- Docker Engine 26.1.5（WSL Debian 内，`docker` 组已包含当前用户）；原生 PostgreSQL 17.12 + PostGIS 3.5 占用端口 5432，`psql` 17.12。
- 数据库 `openalex`（原生 PG 17）已导入 `schema.sql` + 三人小样种子 + 四份留痕种子（220 条 API 请求 / 30 条事件证据 / 19 项规则测试）；`verify_poc.sql` 全部检查为 `OK`。
- AGE 容器 `openalex-pg` 已启动，镜像 `apache/age:release_PG16_1.6.0`（479 MB），端口 **5433→5432**，容器内补装 PostGIS 3.6.4（3.7 与 PG16 二进制不兼容）。`schema.sql` + 种子 + 图投影 + 四份留痕种子 + `verify_poc.sql` 全部执行成功，V1–V5 图查询输出与文档预期一致。
- 口令保存在 WSL 的 `/home/v/.openalex_db_pass`（权限 600）。连接示例：
  `PGPASSWORD=$(cat ~/.openalex_db_pass) psql -h 127.0.0.1 -U postgres -d openalex`（原生 5432），
  `PGPASSWORD=$(cat ~/.openalex_db_pass) psql -h 127.0.0.1 -p 5433 -U postgres -d openalex`（AGE 容器）。
- AGE 镜像的旧 tag `PG16_latest` 已失效，正确 tag 为 `release_PG16_1.6.0`。若 Docker Hub 直连超时，用 `docker.1ms.run` 镜像站拉取后 `docker tag`。

### 1,000 人试跑落库与分析层（原生 PG17:5432，2026-11-22）

`pipeline/`（WP0–WP11）已在原生 PG17 的 `openalex` 库完成 1,000 人试跑落库并跑通分析层，规模约 2.1 GB：

| 表 | 行数 | 说明 |
|---|---|---|
| `work` | 865,111 | 抓取 922,627 篇按 openalex_id 去重 |
| `scholar` | 896 | 3 种子 + 893 新（严格 A 级定案） |
| `identity_map` | 1,700 | openalex 896（A718/B178）+ orcid 804 |
| `authorship` | 846,012 | 覆盖全部 896 学者 |
| `country` / `institution` | 203 / 31,388 | 机构全部带 ROR |
| `institution_alias` | 1,815,799 | 全 confirmed |
| 已回填 `matched_institution_id` | 646,069 | 占署名 76.37%、占有署名串者 95.85%，孤儿外键 0 |
| `annual_panel` / `affiliation_share` | 42,046 / 96,481 | 年度众数主机构、并列标记、机构署名占比 |
| `mobility_event` / `event_evidence` | 13,355 / 53,425 | C1–C5 事件与四判据 + external_cv 证据，规则版本 `A_1000_v1` |
| `class_rule` / `mobility_class` | 8 / 896 | 8.5.2 权威 D/I/S→M1..M8 映射；631 可正式定类 / 265 标不确定 |

分析层边界：13,350 条事件无真实任职年份（签名为代理年），5 条 Confirmed 借用 3 人小样履历；八类分类依据原始 Word `科学家空间流动数据库建立操作1030.docx` 8.5.2 节（此前"8.5 全表未提供"是只看了设计文档的不完整结论），D/I 只认 Confirmed/Probable，阴性维度有 Possible 未决候选者 `formal_class` 留空（265 人）；「中国出生、现居美国」队列无数据源（`birth_country_hint` 全 NULL），DID 未做。运行顺序、断点续跑、回归测试、额度自查与全部踩坑详见 `pipeline/README.md`。

## 技术栈

| 层次 | 工具 |
|------|------|
| 抓取 | Python (requests/httpx)、OpenAlex API、ROR、ORCID、Crossref |
| 存储 | PostgreSQL 17（原生落库）+ PostgreSQL 16 + PostGIS / Apache AGE（图容器） |
| 计算 | pandas/polars、statsmodels/linearmodels (DID)、pytest |
| 可视化 | Leaflet / ECharts / Plotly |
| 交付 | Word、LaTeX、Zotero/BibTeX、Docker |

---

*本仓库为项目研究资料归档，原始数据与文档版权归委托方所有。*
