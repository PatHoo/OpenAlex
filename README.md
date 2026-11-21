# OpenAlex 科学家空间流动数据库

**全球高被引学者的机构隶属与空间流动轨迹重建** — 数据基建 · 政策研究 · AI 科研工具平台

> **项目状态**：第一期方法可行性验证（Proof of Concept）已完成。全量 10.5 万学者跑批、DID 分析与 Agent 平台尚未实施，详见 [项目状态](#项目状态)。

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
│   ├── 三人小样原型_部署文档.md
│   └── 三人小样原型_部署文档_Docker_WSL版.md
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

- **PostgreSQL 16 + PostGIS**：18 张表覆盖身份、作品署名、机构地理、年度面板、事件证据分类、请求日志。`rule_version` 进入事件/分类表主键，A 轨以触发器锁定不可变。
- **Apache AGE**：8 类节点 / 14 种边的图投影，时间作为 `AFFILIATED` 边属性、规则版本作版本属性。图随时可 `drop_graph` 重建，不影响原始数据。
- **分工**：统计判定（众数、占比、C2 抑制、阈值敏感性）全在 SQL；迁移轨迹、改名链、合作网络等路径查询走图；DID 回归导出到 pandas/statsmodels。

## 快速开始

三人小样原型已备好可执行的建表、种子与图投影脚本。Docker + WSL 环境：

```bash
docker pull apache/age:PG16_latest
docker run -d --name openalex-pg -e POSTGRES_PASSWORD=openalex123 \
  -e POSTGRES_DB=openalex -p 5432:5432 apache/age:PG16_latest

docker cp design/schema.sql               openalex-pg:/tmp/schema.sql
docker cp design/三人小样_seed.sql          openalex-pg:/tmp/seed.sql
docker cp design/三人小样_AGE投影.sql        openalex-pg:/tmp/graph.sql

docker exec openalex-pg psql -U postgres -d openalex -f /tmp/schema.sql
docker exec openalex-pg psql -U postgres -d openalex -f /tmp/seed.sql
docker exec openalex-pg psql -U postgres -d openalex -f /tmp/graph.sql
```

导入后种子数据为：3 学者 / 11 机构 / 139 年度面板 / 126 署名占比 / 6 条迁移事件 / 4 条外部履历 / 9 条分类结果。跑 `三人小样原型_部署文档_Docker_WSL版.md` 第 7.1 节的 V1–V5 图查询，应看到 Newman 的完整轨迹与三人分类现状。

## 项目状态

**已完成**：三人小样的全链路验证（身份匹配 → 抓取留痕 → 机构标准化 → 年度面板 → C1–C5 事件 → 履历佐证 → 轨迹矩阵），19 项规则测试通过，证据链做到 API 级留痕（URL、时间戳、HTTP 状态、sha256）。

**未完成 / 边界**：

- 验证样本为 Becke、Perdew、Newman 三人，占 10.5 万目标学者约 **0.003%**，属 Proof of Concept 而非全量成果。
- Newman 证据完整、判为 M8；Becke / Perdew 因任职年份缺失与并列年，`mobility_class` 正式字段留空。
- 6 条有效迁移中，2 条用署名年代理、无真实任职年份；70% 阈值下部分事件从 Probable 降为 Possible。
- 「中国出生、现居美国」人群筛选、DID 分析、文献模块、论文写作、本地化部署尚未实施。
- 未做用户人工抽样审计；报告作者主动声明「只完成小样本，不可宣称全方案通过」。

因此本仓库的材料定位为**可审计的小样施工成果 + 证据边界声明**，而非验收通过报告。`output/` 中的验收质疑清单据此整理了付款前需澄清的事项。

## 技术栈

| 层次 | 工具 |
|------|------|
| 抓取 | Python (requests/httpx)、OpenAlex API、ROR、ORCID、Crossref |
| 存储 | PostgreSQL 16 + PostGIS、Apache AGE |
| 计算 | pandas/polars、statsmodels/linearmodels (DID)、pytest |
| 可视化 | Leaflet / ECharts / Plotly |
| 交付 | Word、LaTeX、Zotero/BibTeX、Docker |

---

*本仓库为项目研究资料归档，原始数据与文档版权归委托方所有。*
