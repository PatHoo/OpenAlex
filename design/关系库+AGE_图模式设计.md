# 科学家空间流动数据库：关系库 + Apache AGE 图模式设计

版本：v1.0 · 2026-11-21
适用范围：OpenAlex 高被引科学家机构隶属—空间流动数据库，第一期（数据地基）与第二期（事件引擎）
配套脚本：`schema.sql`（本文所有 DDL 的可执行版本）

---

## 0. 五条设计原则

1. **关系库是唯一真相源（single source of truth）**，图库只是可随时重建的派生投影。所有事件判定、证据等级、阈值版本只在关系库落账；图里的数据允许被丢弃重算。
2. **A/B 双轨物理隔离**：`rule_version` 进入主键，基线 A（60%）与敏感性 B（50%/70%）分行存储，B 永远不 UPDATE 覆盖 A。证据卡可复用，判定结果不可复用。
3. **可审计优先于查询性能**：每条事件必须能回溯到论文年、履历年、gap、来源 URL、sha256、HTTP 状态、抓取时间。
4. **禁用 LOCF 年作事件证据**：面板表设 `is_imputed` 标志，所有事件识别 SQL 强制过滤该标志。
5. **能力分层**：众数、占比、连续两年、DID 回归属于"集合计算"，留在 SQL；路径遍历、多跳探索、网络分析交给 Cypher。不要试图用一种模型干完所有活。

---

## 1. 总体架构与数据流

```
OpenAlex / ROR / ORCID / Crossref
        │  抓取（带 sha256、状态码、时间戳留痕）
        ▼
[关系库层] api_request → raw_work → work / authorship / institution / alias
        │  年度面板构建（众数、占比、并列、LOCF 标记）
        ▼
   annual_panel + affiliation_share
        │  C1–C5 识别（三指标 → 履历核验 → 分级）
        ▼
   mobility_event (rule_version=A/B50/B70) + event_evidence + mobility_class
        │  增量投影（只导出已确认字段）
        ▼
[AGE 图投影层] Scholar / Institution / Country / Work / Event / Alias / CareerFact
        │
        ▼
  轨迹查询 · 合作网络 · 改名链 · 地图可视化 · DID 导出
```

**为什么图是投影而非主库**：Apache AGE 的属性没有唯一约束和外键语义，图里无法保证"一个学者只有一个 sid""一条事件只算一次"；而项目的核心是证据可复现。因此所有约束、幂等、版本控制都在关系层完成，图只做批量只读重建。

---

## 2. 关系库表结构（PostgreSQL 16）

共 18 张表，分五组。完整可建表脚本见 `schema.sql`。

### 2.1 身份与名单（source / identity）

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| `source_scholar` | `src_id` | `source_row`, `authfull`, `inst_name`, `cntry`, `np6017`, `firstyr`, `lastyr`, `h17`, `citations`, `discipline` | Table-S1 原始 105,027 行，原样保留不清洗 |
| `scholar` | `scholar_id` | `src_id`, `display_name`, `birth_country_hint`, `birth_evidence`, `is_active` | 规范化后的学者主表。出生国只作"提示"，需证据才能进 DID 队列 |
| `identity_map` | `(scholar_id, scheme, external_id)` | `scheme`∈{openalex, orcid, scopus}, `match_level`∈{A,B}, `evidence_url`, `matched_at` | 身份映射。A 级为强匹配，B 级需人工复核 |
| `name_variant` | `variant_id` | `scholar_id`, `raw_name`, `source`, `is_primary` | 姓名/拼音变体，供"中国出生"筛查与消歧 |

### 2.2 作品与署名（work / authorship）

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| `work` | `work_id` | `openalex_id` UNIQUE, `doi`, `title`, `pub_year`, `work_type`, `cited_by_count`, `is_preprint`, `raw_sha256` | 预印本跨版本不自动合并（R5），另记 `doi_version_of` |
| `authorship` | `authorship_id` | `work_id`, `scholar_id`, `author_position`, `raw_name`, `raw_affiliation`, `matched_institution_id`, `is_valid` | 一条署名 = 一条记录；`is_valid=false` 的不计入占比分母 |
| `doi_duplicate` | `dup_id` | `doi`, `work_id_a`, `work_id_b`, `resolution`, `note` | 同 DOI 去重记录，保留结构化署名更丰富的那条（R6） |

### 2.3 机构与地理（institution / geo）

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| `country` | `country_code` | `name_zh`, `name_en`, `iso3` | 国家字典 |
| `institution` | `institution_id` | `ror_id` UNIQUE, `canonical_name`, `country_code`, `city`, `lat`, `lon`, `parent_id` | ROR 归一机构；城市经纬度供地图 |
| `institution_alias` | `alias_id` | `raw_name`, `institution_id`, `status`∈{confirmed,proposed,unresolved}, `evidence_url`, `sha256` | C5 的关键表。112 条"可能名称变化"先落 `proposed`，不证实不改判 |
| `institution_rename` | `rename_id` | `from_alias`, `to_alias`, `same_ror_id`, `verified`, `observed_year` | 改名链；`same_ror_id=true` 时明确不计迁移 |

### 2.4 面板与占比（panel）

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| `annual_panel` | `(scholar_id, year)` | `primary_institution_id`, `primary_country`, `primary_is_tie`, `tie_count`, `secondary_countries`(数组), `valid_works`, `is_imputed`, `observation_status` | 主机构/主国家**分别独立**按年度众数计算，允许并列（`primary_is_tie=true` 不强选） |
| `affiliation_share` | `(scholar_id, year, institution_id)` | `work_count`, `share`, `country_code`, `is_primary` | 占比分母：`share = work_count / 该年有效署名总数`。R11 明确这是实施方口径，非用户原文参数，故单独存、可换算法 |

### 2.5 事件、证据、分类（event / evidence / class）

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| `mobility_event` | `event_id` | `scholar_id`, `event_type`∈{C1..C5}, `paper_year`, `actual_year`, `gap_years`, `from_inst`, `to_inst`, `from_country`, `to_country`, `status`∈{pending,confirmed,not_sustained}, `evidence_level`∈{Confirmed,Probable,Possible,Rejected}, `year_source`∈{signature_proxy,external_actual}, `rule_version`, `is_suppressed`, `suppressed_by` | 事件主表。`rule_version` 参与唯一约束，A/B 不互覆盖 |
| `event_evidence` | `evidence_id` | `event_id`, `criterion`∈{two_year_dominant,share_60,min3works,external_cv}, `passed`, `measured_value`, `threshold`, `source_url`, `source_sha256`, `http_status` | 三指标 + 外部履历逐条落账，B 轨复用 A 的 evidence_id |
| `career_fact` | `fact_id` | `scholar_id`, `fact_year`, `institution_id`, `country_code`, `source_type`∈{cv,orcid,homepage,award}, `source_url`, `sha256`, `verified` | 外部履历。±2 年匹配后以 `fact_year` 覆盖论文年 |
| `mobility_class` | `(scholar_id, rule_version)` | `D`, `I`, `S`, `observed_class`, `formal_class`, `is_classified`, `uncertainty_reason` | `formal_class` 允许 NULL——Becke/Perdew 的情况正式字段必须留空，只在 `observed_class` 写 M6 |
| `class_rule` | `(d,i,s)` | `class_code`∈{M1..M8}, `label_zh`, `source_section` | D/I/S 到八类的映射表。**具体映射须从方案 8.5 节回填**，本文不臆造未知组合；仅已知 `D=1,I=1,S=1 → M8` |

### 2.6 抓取日志与分析

| 表 | 主键 | 说明 |
|---|---|---|
| `api_request` | `request_id` | 65 条请求原型：URL、时间戳、状态码、sha256、字节数、后端路径（对应 `/srv/catsco-agent/work/...`）、429/401 错误码 |
| `rule_test` | `(test_name, rule_version)` | 19 项规则测试结果，含 `test_c1`、`test_c2_suppresses_c1`、`test_threshold_boundary` 等 |
| `cohort_member` | `scholar_id` | "中国出生、现居美国"队列：`birth_evidence`, `determination_method`, `confidence`, `reviewed_by` |
| `did_panel` | `(scholar_id, year)` | DID 用面板：`treated`, `post`(2018+), `outcome_moves`, `covariates`(JSONB) |

---

## 3. 关键口径在关系层的落地

### 3.1 年度面板（众数 + 并列 + LOCF）

```sql
-- 主机构按年度众数；并列时 primary_is_tie=true，不强选
INSERT INTO annual_panel (scholar_id, year, primary_institution_id,
                          primary_country, primary_is_tie, tie_count,
                          valid_works, is_imputed, observation_status)
SELECT scholar_id, pub_year, institution_id, country_code,
       (cnt = max_cnt) AS primary_is_tie,
       (SELECT count(*) FROM t t2
        WHERE t2.scholar_id=t.scholar_id AND t2.pub_year=t.pub_year
          AND t2.cnt=t.max_cnt) AS tie_count,
       total_valid, false, 'observed'
FROM (
  SELECT a.scholar_id, w.pub_year, a.matched_institution_id AS institution_id,
         i.country_code, count(*) AS cnt,
         max(count(*)) OVER (PARTITION BY a.scholar_id, w.pub_year) AS max_cnt,
         sum(count(*)) OVER (PARTITION BY a.scholar_id, w.pub_year) AS total_valid
  FROM authorship a JOIN work w USING (work_id)
  JOIN institution i ON i.institution_id=a.matched_institution_id
  WHERE a.is_valid=true AND w.work_type<>'preprint'
  GROUP BY 1,2,3,4
) t;
```

LOCF 填充年用单独写入并设置 `is_imputed=true, observation_status='imputed'`，**永不**进入下面任何事件识别。

### 3.2 C1–C5 识别顺序（C2 优先于 C1，C5 先消解）

```sql
-- Step 0：C5 改名消解——同一 ROR 的名称变化先归一化，不产生迁移
UPDATE authorship a SET matched_institution_id = r.to_institution_id
FROM institution_rename r
WHERE r.verified=true AND r.same_ror_id=true
  AND a.matched_institution_id = r.from_institution_id;

-- Step 1：候选事件（禁止 imputed 年入内）
CREATE VIEW v_event_candidate AS
WITH p AS (
  SELECT *, lag(primary_institution_id) OVER w AS prev_inst,
            lag(primary_country)       OVER w AS prev_country,
            lag(primary_is_tie)        OVER w AS prev_tie,
            lead(primary_country)      OVER w AS next_country
  FROM annual_panel
  WHERE is_imputed=false
  WINDOW w AS (PARTITION BY scholar_id ORDER BY year)
)
SELECT scholar_id, year AS paper_year,
  CASE
    WHEN primary_country <> prev_country AND prev_tie=false AND primary_is_tie=false
      THEN 'C2'                                   -- 国际迁移，优先
    WHEN primary_institution_id <> prev_inst
      AND primary_country = prev_country
      AND prev_tie=false AND primary_is_tie=false
      THEN 'C1'                                   -- 国内机构迁移
    WHEN NOT (secondary_countries @> ARRAY[primary_country])
      AND EXISTS (SELECT 1 FROM unnest(secondary_countries) s
                  WHERE s <> prev_country AND s <> primary_country)
      THEN 'C3'                                   -- 新增次级国家
    WHEN prev_country = ANY(secondary_countries)
      THEN 'C4'                                   -- 原主国降级为次级并持续
  END AS event_type,
  prev_inst AS from_inst, primary_institution_id AS to_inst,
  prev_country AS from_country, primary_country AS to_country,
  next_country
FROM p
WHERE primary_country IS DISTINCT FROM prev_country
   OR primary_institution_id IS DISTINCT FROM prev_inst
   OR cardinality(secondary_countries) > 0;
```

C2 对 C1 的抑制在写入时统一处理：同年同人若存在 C2，则 C1 写 `is_suppressed=true, suppressed_by=<C2 event_id>`。

C4 三态由持续性决定：首年 `pending`；次年 `prev_country` 仍在 `secondary_countries` 中 → `confirmed`；否则 `not_sustained`。

### 3.3 三指标 → 证据等级 → 履历覆盖

```sql
-- 三指标（阈值参数化，A=0.60，B50=0.50，B70=0.70）
INSERT INTO event_evidence (event_id, criterion, passed, measured_value, threshold, rule_version)
SELECT e.event_id, 'two_year_dominant',
       (p.primary_is_tie=false AND p2.primary_is_tie=false
        AND p.primary_institution_id=p2.primary_institution_id),
       2, 2, e.rule_version
FROM mobility_event e
JOIN annual_panel p  ON p.scholar_id=e.scholar_id AND p.year=e.paper_year
JOIN annual_panel p2 ON p2.scholar_id=e.scholar_id AND p2.year=e.paper_year-1;

-- 占比与累计篇数同理，threshold 取 rule_version 对应参数
-- 三指标全过 → Probable；外部履历在 ±2 年内确认 → Confirmed
UPDATE mobility_event e SET
  evidence_level = CASE WHEN cf.fact_id IS NOT NULL THEN 'Confirmed' ELSE 'Probable' END,
  actual_year    = COALESCE(cf.fact_year, e.paper_year),
  gap_years      = ABS(COALESCE(cf.fact_year, e.paper_year) - e.paper_year),
  year_source    = CASE WHEN cf.fact_id IS NOT NULL THEN 'external_actual'
                        ELSE 'signature_proxy' END
FROM career_fact cf
WHERE cf.scholar_id=e.scholar_id AND cf.verified=true
  AND ABS(cf.fact_year - e.paper_year) <= 2
  AND cf.institution_id IN (e.from_inst, e.to_inst);
```

注意上例是"有履历"分支；无履历时 `evidence_level` 由三指标结果决定（任一不过 → Possible；访问/兼职/数据错误 → Rejected）。`signature_proxy` 表示年份只是署名代理年——Becke 的 2 条 C1 就属于这种，`actual_year` 必须留空而不是填代理年。

### 3.4 A/B 不覆盖的实现

```sql
ALTER TABLE mobility_event ADD CONSTRAINT uq_ev
  UNIQUE (scholar_id, event_type, paper_year, rule_version);
-- 应用层规则：rule_version='A_base_060' 的行一旦写入禁止 UPDATE/DELETE
-- 可用触发器强制；B 轨只 INSERT，不修改 A 行
CREATE TRIGGER trg_a_immutable BEFORE UPDATE OR DELETE ON mobility_event
  FOR EACH ROW WHEN (OLD.rule_version='A_base_060')
  EXECUTE FUNCTION reject_a_mutation();
```

---

## 4. AGE 图模式

### 4.1 图与标签

```sql
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

SELECT create_graph('mobility');
SELECT create_vlabel('mobility','Scholar');
SELECT create_vlabel('mobility','Institution');
SELECT create_vlabel('mobility','Country');
SELECT create_vlabel('mobility','Work');
SELECT create_vlabel('mobility','Event');
SELECT create_vlabel('mobility','Alias');
SELECT create_vlabel('mobility','CareerFact');
SELECT create_elabel('mobility','AFFILIATED');
SELECT create_elabel('mobility','AUTHORED');
SELECT create_elabel('mobility','SIGNED_AT');
SELECT create_elabel('mobility','LOCATED_IN');
SELECT create_elabel('mobility','EXPERIENCED');
SELECT create_elabel('mobility','FROM_INST');
SELECT create_elabel('mobility','TO_INST');
SELECT create_elabel('mobility','FROM_COUNTRY');
SELECT create_elabel('mobility','TO_COUNTRY');
SELECT create_elabel('mobility','CORROBORATED_BY');
SELECT create_elabel('mobility','RENAMED_TO');
SELECT create_elabel('mobility','IN_COHORT');
```

### 4.2 节点属性

| 标签 | 唯一键（关系库侧保证） | 属性 |
|---|---|---|
| `Scholar` | `sid` | `sid`, `name`, `orcid`, `openalex_id`, `birth_hint`, `h17` |
| `Institution` | `iid` | `iid`, `ror_id`, `name`, `country`, `city`, `lat`, `lon` |
| `Country` | `code` | `code`, `name_zh`, `name_en` |
| `Work` | `wid` | `wid`, `doi`, `year`, `title`, `type` |
| `Event` | `eid` | `eid`, `type`, `paper_year`, `actual_year`, `gap`, `level`, `status`, `year_source`, `rv` |
| `Alias` | `aid` | `aid`, `raw_name`, `status` |
| `CareerFact` | `fid` | `fid`, `year`, `source_type`, `url`, `sha256` |

### 4.3 边与方向

| 边 | 起点 → 终点 | 属性 | 用途 |
|---|---|---|---|
| `AFFILIATED` | Scholar → Institution | `year`, `role`∈{primary,secondary}, `share`, `works`, `is_imputed`, `rv` | 年度任职轨迹；同一对节点多年份多条边 |
| `AUTHORED` | Scholar → Work | — | 合作网络底边 |
| `SIGNED_AT` | Work → Institution | `year`, `raw_affiliation`, `is_valid` | 署名证据，回溯到原始字符串 |
| `LOCATED_IN` | Institution → Country | — | 国家归属 |
| `EXPERIENCED` | Scholar → Event | — | 经历事件 |
| `FROM_INST` / `TO_INST` | Event → Institution | — | 迁移起止机构 |
| `FROM_COUNTRY` / `TO_COUNTRY` | Event → Country | — | 迁移起止国家 |
| `CORROBORATED_BY` | Event → CareerFact | — | 履历佐证（Confirmed 的依据） |
| `RENAMED_TO` | Alias → Institution | `verified`, `observed_year` | C5 改名链，连接别名与归一机构 |
| `IN_COHORT` | Scholar → Country | `method`, `confidence` | "现居美国"队列标记 |

**时间与版本建模选择**：时间作为边属性而非时间节点。理由——本项目 99% 的查询是"某人在某年在哪"和"按年排序的轨迹"，边属性过滤最直接；若改成时间节点，(Scholar)-[:AT]->(Year)<-[:OF]-(Institution) 会让每次查询多一跳且无法表达"占比"。版本 `rv` 同理放边/事件属性，B 轨投影到独立事件节点，不覆盖 A。

---

## 5. C1–C5 在两种模型里如何落地（核心对照）

| 事件 | 关系库（权威判定） | AGE 图（投影与查询） |
|---|---|---|
| **C5 改名** | `institution_rename.verified=true AND same_ror_id=true` 先把署名归一到 `to_institution_id`，**不生成事件行**；未证实的 112 条留 `proposed` | `(Alias)-[:RENAMED_TO {verified:true}]->(Institution)`；查询改名链时走 Alias 节点，图中不存在"C5 迁移事件"节点——这正是设计意图 |
| **C2 国际迁移** | `primary_country` 变更且两端非并列 → 写 `event_type='C2'`；同年 C1 置 `is_suppressed=true` | `(Event{type:'C2'})-[:FROM_COUNTRY]->(Country), -[:TO_COUNTRY]->(Country)`，并 `(:Scholar)-[:EXPERIENCED]->(Event)`；C2 抑制关系不单独建边，用 `Event.is_suppressed` 属性表达 |
| **C1 国内迁移** | `primary_institution_id` 变、`primary_country` 不变、两端非并列，且未被 C2 抑制 → 写 C1 | 同一 Event 模型，`FROM_INST`/`TO_INST` 指向同一国家内两机构；配合 `AFFILIATED.year` 可还原"1983/1986/2007"这类只有代理年、无 `actual_year` 的事件 |
| **C3 新增次级国家** | `secondary_countries` 出现新国家且主国家不变 → 写 C3，`from_country=to_country=primary_country` | `(:Scholar)-[:AFFILIATED {role:'secondary'}]->(:Institution)` 新边；Event 的 `TO_COUNTRY` 指向新增国，`FROM_COUNTRY` 可省略 |
| **C4 主国降级为次级** | 前一主国出现在 `secondary_countries` → `status` 从 `pending` 经 `confirmed`/`not_sustained` 流转 | 图上表现为：高年份 `AFFILIATED{role:'primary'}` 边减少、`{role:'secondary'}` 边增加；`Event.status` 三态直接可读 |

**一句话分工**：C1–C5 的**判定**全部在 SQL（因为要算众数、占比、并列、抑制、阈值版本）；图只负责把已判定的事件**表达成可遍历的路径**，并回答"这个人这一生怎么走的""谁和谁走同一条路线"这类问题。

### 5.1 同一事件的两种查询示例

关系库版（权威审计视图）：

```sql
SELECT event_type, paper_year, actual_year, gap_years, evidence_level,
       year_source, rule_version, is_suppressed
FROM mobility_event
WHERE scholar_id = (SELECT scholar_id FROM identity_map
                    WHERE scheme='openalex' AND external_id='A2832473')  -- Newman 示意
  AND rule_version='A_base_060'
ORDER BY paper_year;
```

图版（轨迹 + 国家 + 证据一跳取回）：

```cypher
MATCH (s:Scholar {sid:$sid})-[:EXPERIENCED]->(e:Event)
MATCH (e)-[:FROM_INST]->(fi:Institution), (e)-[:TO_INST]->(ti:Institution)
MATCH (e)-[:TO_COUNTRY]->(c:Country)
OPTIONAL MATCH (e)-[:CORROBORATED_BY]->(f:CareerFact)
RETURN e.type AS type, e.paper_year AS paperYear, e.actual_year AS actualYear,
       e.level AS level, e.year_source AS source,
       fi.name AS fromInst, ti.name AS toInst, c.name_zh AS toCountry,
       f.url AS evidence
ORDER BY e.paper_year;
```

版本切换只需换 `rv` 过滤条件，A/B 两套事件节点在图里并存，互不覆盖。

### 5.2 图才擅长的查询（关系库写起来很痛）

```cypher
-- Newman 的完整任职轨迹（含经纬度，直接喂地图）
MATCH (s:Scholar {sid:$sid})-[a:AFFILIATED {role:'primary'}]->(i:Institution)
WHERE a.is_imputed=false AND a.rv='A_base_060'
RETURN a.year AS year, i.name AS inst, i.city AS city, i.lat AS lat, i.lon AS lon
ORDER BY a.year;

-- 改名链：C5 观察的 112 条如何收敛到一个 ROR
MATCH p=(al:Alias)-[:RENAMED_TO*1..4]->(i:Institution)
WHERE i.ror_id = $ror
RETURN [n IN nodes(p) | coalesce(n.raw_name, n.name)] AS chain;

-- 在美中国出生学者中，谁经历过 C2 且合作者最多
MATCH (s:Scholar)-[:IN_COHORT]->(:Country {code:'US'})
MATCH (s)-[:EXPERIENCED]->(e:Event {type:'C2', level:'Confirmed'})
OPTIONAL MATCH (s)-[:AUTHORED]->(w:Work)<-[:AUTHORED]-(other:Scholar)
RETURN s.name, count(DISTINCT other) AS collaborators
ORDER BY collaborators DESC LIMIT 20;
```

---

## 6. 关系库 → AGE 投影流程

1. **全量首次构建**：先建节点（Scholar/Institution/Country/Work/Alias/CareerFact），再建边（AFFILIATED/AUTHORED/...），最后建 Event 节点及关联边。顺序不可颠倒，边依赖两端节点。
2. **增量更新**：`mobility_event` 新增行 → 只投影对应 Event 节点与 5 条关联边；`annual_panel` 变更 → 删除并重插该学者的 AFFILIATED 边（AGE 没有 UPSERT，先 `DETACH DELETE` 相关边再 CREATE）。
3. **批量装载**：SQL 侧用 `jsonb_agg` 聚合行，Cypher 侧 `UNWIND $rows AS row CREATE ...`，避免逐行调用。
4. **一致性校验**：每晚跑一次对账——关系库 `count(*)` 与图中 `MATCH (n:Event) RETURN count(n)` 比对，不一致即触发全量重建。
5. **重建脚本**：`drop_graph('mobility', true)` → 重建标签 → 重跑投影。**丢弃图是安全操作**，因为真相源在关系库。

```cypher
// 批量边投影示意（params 由 SQL 的 jsonb_agg 生成）
UNWIND $rows AS row
MATCH (s:Scholar {sid: row.sid}), (i:Institution {iid: row.iid})
CREATE (s)-[:AFFILIATED {
  year: row.year, role: row.role, share: row.share,
  works: row.works, is_imputed: row.is_imputed, rv: row.rv
}]->(i)
```

---

## 7. 索引与性能

| 层 | 索引 | 理由 |
|---|---|---|
| 关系库 | `annual_panel(scholar_id, year)` 主键索引 | 窗口函数与面板读取 |
| 关系库 | `mobility_event(scholar_id, rule_version, paper_year)` | 双轨查询主路径 |
| 关系库 | `authorship(work_id)`、`authorship(scholar_id)` | 占比与作品聚合 |
| 关系库 | `affiliation_share(scholar_id, year)` | 60% 阈值计算 |
| AGE | `CREATE INDEX ON "mobility"."Scholar" USING btree ((properties->>'sid'));` | sid 精确查找 |
| AGE | `CREATE INDEX ON "mobility"."Event" USING btree ((properties->>'type'));` | C1–C5 过滤 |
| AGE | `CREATE INDEX ON "mobility"."AFFILIATED" USING gin (properties);` | 边属性过滤（版本敏感，需按本机 AGE 验证语法） |

规模估计：10.5 万学者 × 平均 30 年 ≈ 300 万面板行；作品与署名千万级；事件数万级。关系库单机可承载；图投影只导出"已确认 + 近 N 年"，控制在百万边以内。

---

## 8. 已知坑（务必先验证）

1. **AGE 版本差异**：`agtype_build_map`、`jsonb → agtype` 的转换、`->>` 表达式索引在不同版本行为不一。落地第一步写 10 行 smoke test 验证，不要直接跑全量。
2. **无属性唯一约束**：图里可能出现重复节点。幂等靠"先删后建 + 关系库计数对账"，不靠图约束。
3. **并发写**：AGE 的 CREATE 在可重复读隔离下可能报序列化失败。采用单写者批量投影，禁止多进程同时写图。
4. **边必须有标签**：创建边时不能省略标签；顶点可多标签，边只能一个标签。
5. **数值类型**：agtype 中整数与浮点区分，`share=0.6` 要写成 `0.6` 而非 `60`；百分比统一用 0–1 小数存储。
6. **A 轨不可变**：触发器是最后防线，真正保障来自"B 只 INSERT 不 UPDATE"的应用约定与代码审查。
7. **出生国不确定**：`birth_country_hint` 不是可靠字段。"中国出生"筛查必须走 `cohort_member` 的人工复核列，否则 DID 的处理组定义会被质疑。

---

## 9. 从现有 Excel 迁移到新库

现有 `1030版_新三学者基线与增量对照数据.xlsx` 有 25 个 sheet，迁移映射：

| Excel sheet | 目标表 |
|---|---|
| `A身份映射` | `scholar` + `identity_map` + `name_variant` |
| `A生涯汇总` / `B生涯对照` | `mobility_class`（按 rule_version 分两批写入） |
| `A最终有效迁移时间表` / `B时间差诊断` | `mobility_event` + `event_evidence` + `career_fact` |
| `A不确定转换` / `A可能机构名称变化观察` | `annual_panel.primary_is_tie` / `institution_alias(status='proposed')` |
| `A基线请求清单` / `A补查Crossref` | `api_request` |
| `规则测试` | `rule_test` |
| `A DOI去重记录` | `doi_duplicate` |
| `A轨迹_*` | 投影验证集：与图查询结果逐格比对 |

三名学者的 `formal_class` 必须按原样保留 NULL（Becke、Perdew），不得因迁移而"补全"。

---

## 10. 落地顺序与工期

| 步骤 | 内容 | 估时 |
|---|---|---|
| 1 | 安装 PG16 + PostGIS + AGE，跑 smoke test 验证版本相关语法 | 0.5 天 |
| 2 | 执行 `schema.sql`，导入 Table-S1 与三学者 Excel，建立映射 | 2 天 |
| 3 | 实现面板构建 SQL（众数/占比/并列/LOCF），对拍 `A轨迹_*` | 2 天 |
| 4 | 实现 C1–C5 识别 + 三指标 + 履历覆盖，对拍 6 条事件与 19 项测试 | 4 天 |
| 5 | A/B 双轨写入与不可变触发器，对拍 50%/70% 敏感性结果 | 1 天 |
| 6 | 图投影脚本 + 全量/增量重建 + 对账 | 3 天 |
| 7 | 典型查询封装（轨迹、改名链、队列合作网络）与验收 | 2 天 |

合计约 **14–15 个工作日**，不含全量 10.5 万人的抓取与人工履历核验。

---

## 附：验收自检清单

- [ ] 19 项 `rule_test` 全部通过且带 rule_version
- [ ] 6 条事件与 `A最终有效迁移时间表` 逐字段一致（含 4 条 `external_actual`、2 条 `actual_year=NULL`）
- [ ] Becke/Perdew 的 `mobility_class.formal_class` 仍为 NULL
- [ ] A 轨行在 B 轨重算后哈希不变
- [ ] 图中 Event 节点数 = 关系库事件行数（同一 rule_version 下）
- [ ] 禁用 LOCF 年：任意事件 `paper_year` 对应的 `annual_panel.is_imputed=false`
