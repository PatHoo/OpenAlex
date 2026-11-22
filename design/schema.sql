-- ============================================================
-- 科学家空间流动数据库 · 关系库 + Apache AGE 模式
-- PostgreSQL 16 + PostGIS 3 + AGE 1.x
-- 生成日期：2026-11-21
-- 执行顺序：本文件 Part 1-6c 为关系库；Part 7 仅为 AGE 目标模式说明。
-- 小样图投影请执行 三人小样_AGE投影.sql，不要再取消 Part 7 注释，以免标签重复创建。
-- ============================================================

-- PostGIS 仅用于未来几何字段；当前 schema 未使用几何类型。
-- AGE 官方镜像不一定带 PostGIS，因此仅在扩展可用时安装，避免阻断建表。
DO $ext$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'postgis') THEN
        BEGIN
            CREATE EXTENSION IF NOT EXISTS postgis;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'postgis 扩展存在但无法加载（%），已跳过；当前 schema 未使用几何类型。', SQLERRM;
        END;
    ELSE
        RAISE NOTICE 'postgis 扩展不可用，已跳过；当前 schema 未使用几何类型。';
    END IF;
END
$ext$;

-- ---------- Part 1: 字典 ----------
CREATE TABLE country (
    country_code  CHAR(2) PRIMARY KEY,
    name_zh       TEXT NOT NULL,
    name_en       TEXT,
    iso3          CHAR(3)
);

CREATE TABLE institution (
    institution_id   BIGSERIAL PRIMARY KEY,
    ror_id           TEXT UNIQUE,
    canonical_name   TEXT NOT NULL,
    country_code     CHAR(2) REFERENCES country(country_code),
    city             TEXT,
    lat              NUMERIC(9,6),
    lon              NUMERIC(9,6),
    geom             GEOGRAPHY(POINT, 4326),
    parent_id        BIGINT REFERENCES institution(institution_id),
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_inst_country ON institution(country_code);
CREATE INDEX idx_inst_name ON institution(canonical_name);

CREATE TABLE institution_alias (
    alias_id       BIGSERIAL PRIMARY KEY,
    raw_name       TEXT NOT NULL,
    institution_id BIGINT REFERENCES institution(institution_id),
    status         TEXT NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('confirmed','proposed','unresolved')),
    evidence_url   TEXT,
    sha256         CHAR(64),
    note           TEXT,
    UNIQUE(raw_name, institution_id)
);

CREATE TABLE institution_rename (
    rename_id       BIGSERIAL PRIMARY KEY,
    from_alias_id   BIGINT REFERENCES institution_alias(alias_id),
    to_institution_id BIGINT NOT NULL REFERENCES institution(institution_id),
    same_ror_id     BOOLEAN NOT NULL DEFAULT false,
    verified        BOOLEAN NOT NULL DEFAULT false,
    observed_year   SMALLINT,
    evidence_url    TEXT
);

-- ---------- Part 2: 身份与名单 ----------
CREATE TABLE source_scholar (
    src_id      BIGSERIAL PRIMARY KEY,
    source_row  INTEGER UNIQUE NOT NULL,
    authfull    TEXT,
    inst_name   TEXT,
    cntry       CHAR(2),
    np6017      INTEGER,
    firstyr     SMALLINT,
    lastyr      SMALLINT,
    h17         INTEGER,
    citations   BIGINT,
    discipline  TEXT,
    raw_payload JSONB
);

CREATE TABLE scholar (
    scholar_id         BIGSERIAL PRIMARY KEY,
    src_id             BIGINT UNIQUE REFERENCES source_scholar(src_id),
    display_name       TEXT NOT NULL,
    birth_country_hint CHAR(2) REFERENCES country(country_code),
    birth_evidence     TEXT,
    is_active          BOOLEAN DEFAULT true,
    created_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE identity_map (
    scholar_id  BIGINT NOT NULL REFERENCES scholar(scholar_id),
    scheme      TEXT NOT NULL CHECK (scheme IN ('openalex','orcid','scopus')),
    external_id TEXT NOT NULL,
    match_level CHAR(1) NOT NULL DEFAULT 'B' CHECK (match_level IN ('A','B')),
    evidence_url TEXT,
    matched_at  TIMESTAMPTZ,
    PRIMARY KEY (scholar_id, scheme, external_id)
);
CREATE UNIQUE INDEX idx_identity_external ON identity_map(scheme, external_id);

CREATE TABLE name_variant (
    variant_id BIGSERIAL PRIMARY KEY,
    scholar_id BIGINT NOT NULL REFERENCES scholar(scholar_id),
    raw_name   TEXT NOT NULL,
    source     TEXT,
    is_primary BOOLEAN DEFAULT false
);

-- ---------- Part 3: 作品与署名 ----------
CREATE TABLE work (
    work_id         BIGSERIAL PRIMARY KEY,
    openalex_id     TEXT UNIQUE NOT NULL,
    doi             TEXT,
    title           TEXT,
    pub_year        SMALLINT,
    work_type       TEXT DEFAULT 'article',
    cited_by_count  INTEGER,
    is_preprint     BOOLEAN DEFAULT false,
    doi_version_of  BIGINT REFERENCES work(work_id),
    raw_sha256      CHAR(64),
    fetched_at      TIMESTAMPTZ
);
CREATE INDEX idx_work_year ON work(pub_year);
CREATE INDEX idx_work_doi ON work(doi);

CREATE TABLE authorship (
    authorship_id          BIGSERIAL PRIMARY KEY,
    work_id                BIGINT NOT NULL REFERENCES work(work_id),
    scholar_id             BIGINT NOT NULL REFERENCES scholar(scholar_id),
    author_position        TEXT CHECK (author_position IN ('first','middle','last')),
    raw_name               TEXT,
    raw_affiliation        TEXT,
    matched_institution_id BIGINT REFERENCES institution(institution_id),
    is_valid               BOOLEAN NOT NULL DEFAULT true,
    UNIQUE(work_id, scholar_id)
);
CREATE INDEX idx_auth_scholar ON authorship(scholar_id);
CREATE INDEX idx_auth_inst ON authorship(matched_institution_id);

CREATE TABLE doi_duplicate (
    dup_id     BIGSERIAL PRIMARY KEY,
    doi        TEXT NOT NULL,
    work_id_a  BIGINT NOT NULL REFERENCES work(work_id),
    work_id_b  BIGINT NOT NULL REFERENCES work(work_id),
    resolution TEXT,
    note       TEXT
);

-- ---------- Part 4: 面板与占比 ----------
CREATE TABLE annual_panel (
    scholar_id             BIGINT NOT NULL REFERENCES scholar(scholar_id),
    year                   SMALLINT NOT NULL,
    primary_institution_id BIGINT REFERENCES institution(institution_id),
    primary_country        CHAR(2) REFERENCES country(country_code),
    primary_is_tie         BOOLEAN DEFAULT false,
    tie_count              SMALLINT DEFAULT 0,
    secondary_countries    CHAR(2)[] DEFAULT ARRAY[]::CHAR(2)[],
    secondary_institutions BIGINT[] DEFAULT ARRAY[]::BIGINT[],
    valid_works            INTEGER DEFAULT 0,
    is_imputed             BOOLEAN NOT NULL DEFAULT false,
    observation_status     TEXT NOT NULL DEFAULT 'observed'
                           CHECK (observation_status IN ('observed','imputed','unobserved')),
    note                   TEXT,
    PRIMARY KEY (scholar_id, year)
);

-- 数组列无法直接建外键：用触发器保证 secondary_countries 中每个代码都在 country 字典中。
-- 这样 C3/地图按国家维度统计时不会因 CH/DE/FR/PT/SI/HU/IE/CN 等代码漏连接。
CREATE OR REPLACE FUNCTION validate_panel_secondary_countries() RETURNS trigger AS $$
BEGIN
    IF NEW.secondary_countries IS NOT NULL AND EXISTS (
        SELECT 1 FROM unnest(NEW.secondary_countries) AS c(code)
        WHERE NOT EXISTS (SELECT 1 FROM country WHERE country_code = c.code)
    ) THEN
        RAISE EXCEPTION 'unknown country code in annual_panel.secondary_countries: %',
                        NEW.secondary_countries;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_panel_secondary_countries
BEFORE INSERT OR UPDATE OF secondary_countries ON annual_panel
FOR EACH ROW EXECUTE FUNCTION validate_panel_secondary_countries();

CREATE TABLE affiliation_share (
    scholar_id    BIGINT NOT NULL REFERENCES scholar(scholar_id),
    year          SMALLINT NOT NULL,
    institution_id BIGINT NOT NULL REFERENCES institution(institution_id),
    country_code  CHAR(2) REFERENCES country(country_code),
    work_count    INTEGER NOT NULL,
    share         NUMERIC(5,4) NOT NULL CHECK (share >= 0 AND share <= 1),
    is_primary    BOOLEAN DEFAULT false,
    PRIMARY KEY (scholar_id, year, institution_id)
);

-- ---------- Part 5: 事件 / 证据 / 分类 ----------
CREATE TABLE mobility_event (
    event_id       BIGSERIAL PRIMARY KEY,
    scholar_id     BIGINT NOT NULL REFERENCES scholar(scholar_id),
    event_type     CHAR(2) NOT NULL CHECK (event_type IN ('C1','C2','C3','C4','C5')),
    paper_year     SMALLINT NOT NULL,
    actual_year    SMALLINT,
    gap_years      SMALLINT,
    from_inst      BIGINT REFERENCES institution(institution_id),
    to_inst        BIGINT REFERENCES institution(institution_id),
    from_country   CHAR(2) REFERENCES country(country_code),
    to_country     CHAR(2) REFERENCES country(country_code),
    status         TEXT DEFAULT 'pending'
                   CHECK (status IN ('pending','confirmed','not_sustained')),
    evidence_level TEXT NOT NULL DEFAULT 'Possible'
                   CHECK (evidence_level IN ('Confirmed','Probable','Possible','Rejected')),
    year_source    TEXT DEFAULT 'signature_proxy'
                   CHECK (year_source IN ('signature_proxy','external_actual')),
    rule_version   TEXT NOT NULL DEFAULT 'A_base_060',
    is_suppressed  BOOLEAN DEFAULT false,
    suppressed_by  BIGINT REFERENCES mobility_event(event_id),
    note           TEXT,
    created_at     TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_event UNIQUE (scholar_id, event_type, paper_year, rule_version)
);
CREATE INDEX idx_ev_scholar_rv ON mobility_event(scholar_id, rule_version, paper_year);
CREATE INDEX idx_ev_type ON mobility_event(event_type);

CREATE TABLE event_evidence (
    evidence_id     BIGSERIAL PRIMARY KEY,
    event_id        BIGINT NOT NULL REFERENCES mobility_event(event_id),
    criterion       TEXT NOT NULL CHECK (criterion IN
                    ('two_year_dominant','share_threshold','min3works','external_cv','tie_policy')),
    passed          BOOLEAN,
    measured_value  NUMERIC,
    threshold       NUMERIC,
    rule_version    TEXT NOT NULL DEFAULT 'A_base_060',
    source_url      TEXT,
    source_sha256   CHAR(64),
    http_status     SMALLINT,
    observed_at     TIMESTAMPTZ,
    note            TEXT
);
CREATE INDEX idx_ee_event ON event_evidence(event_id);

CREATE TABLE career_fact (
    fact_id        BIGSERIAL PRIMARY KEY,
    scholar_id     BIGINT NOT NULL REFERENCES scholar(scholar_id),
    fact_year      SMALLINT NOT NULL,
    institution_id BIGINT REFERENCES institution(institution_id),
    country_code   CHAR(2) REFERENCES country(country_code),
    source_type    TEXT CHECK (source_type IN ('cv','orcid','homepage','award')),
    source_url     TEXT,
    sha256         CHAR(64),
    verified       BOOLEAN DEFAULT false,
    note           TEXT
);
CREATE INDEX idx_cf_scholar_year ON career_fact(scholar_id, fact_year);

-- D/I/S -> M1..M8 映射。注意：具体映射须按方案 8.5 节回填。
CREATE TABLE class_rule (
    d_flag      SMALLINT NOT NULL CHECK (d_flag IN (0,1)),
    i_flag      SMALLINT NOT NULL CHECK (i_flag IN (0,1)),
    s_flag      SMALLINT NOT NULL CHECK (s_flag IN (0,1)),
    class_code  CHAR(2) NOT NULL CHECK (class_code IN
                ('M1','M2','M3','M4','M5','M6','M7','M8')),
    label_zh    TEXT,
    source_section TEXT DEFAULT '8.5',
    PRIMARY KEY (d_flag, i_flag, s_flag)
);
-- 已知项（来自小样验证）：Newman D=1,I=1,S=1 -> M8
INSERT INTO class_rule(d_flag,i_flag,s_flag,class_code,label_zh)
VALUES (1,1,1,'M8','跨国迁移型');

CREATE TABLE mobility_class (
    classification_id   BIGSERIAL PRIMARY KEY,
    scholar_id          BIGINT NOT NULL REFERENCES scholar(scholar_id),
    rule_version        TEXT NOT NULL DEFAULT 'A_base_060',
    D                   SMALLINT CHECK (D IN (0,1)),
    I                   SMALLINT CHECK (I IN (0,1)),
    S                   SMALLINT CHECK (S IN (0,1)),
    observed_class      CHAR(2),
    formal_class        CHAR(2),   -- 允许 NULL：Becke/Perdew 分类不确定
    is_classified       BOOLEAN DEFAULT false,
    uncertainty_reason  TEXT,
    UNIQUE (scholar_id, rule_version)
);

-- ---------- Part 6: 日志与分析 ----------
CREATE TABLE api_request (
    request_id    BIGSERIAL PRIMARY KEY,
    endpoint      TEXT,
    request_url   TEXT NOT NULL,
    http_method   TEXT DEFAULT 'GET',
    http_status   SMALLINT,
    requested_at  TIMESTAMPTZ,
    response_sha256 CHAR(64),
    byte_count    INTEGER,
    backend_path  TEXT,
    error_code    TEXT,
    payload       JSONB
);
CREATE INDEX idx_api_time ON api_request(requested_at);

CREATE TABLE rule_test (
    test_name    TEXT NOT NULL,
    rule_version TEXT NOT NULL DEFAULT 'A_base_060',
    status       TEXT NOT NULL CHECK (status IN ('passed','failed','skipped')),
    detail       JSONB,
    executed_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (test_name, rule_version)
);

CREATE TABLE cohort_member (
    scholar_id           BIGINT PRIMARY KEY REFERENCES scholar(scholar_id),
    birth_country        CHAR(2) REFERENCES country(country_code),
    current_country      CHAR(2) REFERENCES country(country_code),
    birth_evidence       TEXT,
    determination_method TEXT,
    confidence           TEXT CHECK (confidence IN ('high','medium','low')),
    reviewed_by          TEXT,
    reviewed_at          TIMESTAMPTZ
);

CREATE TABLE did_panel (
    scholar_id          BIGINT NOT NULL REFERENCES scholar(scholar_id),
    year                SMALLINT NOT NULL,
    treated             BOOLEAN NOT NULL,
    post                BOOLEAN NOT NULL,
    outcome_moves       INTEGER,
    outcome_intl_moves  INTEGER DEFAULT 0,
    outcome_cn_link     INTEGER DEFAULT 0,
    outcome_log_works   NUMERIC(10,4),
    covariates          JSONB,
    cohort_confidence   TEXT CHECK (cohort_confidence IN ('high','medium','low')),
    PRIMARY KEY (scholar_id, year)
);

-- 中国背景候选队列的可核查信号；出生国未被算法断言时 cohort_member.birth_country 为 NULL。
CREATE TABLE cohort_evidence (
    evidence_id     BIGSERIAL PRIMARY KEY,
    scholar_id      BIGINT NOT NULL REFERENCES scholar(scholar_id),
    rule_version    TEXT NOT NULL,
    signal_code     TEXT NOT NULL CHECK (signal_code IN
                    ('pinyin_strong','pinyin_weak','gc_primary','gc_secondary',
                     'gc_share','gc_cv_verified','birth_hint',
                     'us_2017_primary','us_fallback')),
    signal_value    TEXT,
    evidence_detail TEXT,
    source_table    TEXT,
    source_key      TEXT NOT NULL DEFAULT '',
    weight          SMALLINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (scholar_id, rule_version, signal_code, source_key)
);

CREATE TABLE did_estimate (
    estimate_id           BIGSERIAL PRIMARY KEY,
    rule_version          TEXT NOT NULL,
    cohort_rule_version   TEXT NOT NULL,
    specification         TEXT NOT NULL CHECK (specification IN ('did_2x2','event_study')),
    outcome               TEXT NOT NULL,
    coefficient           NUMERIC(12,6),
    std_error             NUMERIC(12,6),
    t_stat                NUMERIC(12,6),
    n_treated             INTEGER,
    n_control             INTEGER,
    n_obs                 INTEGER,
    pretrend_coef         NUMERIC(12,6),
    parallel_trend_passed BOOLEAN,
    note                  TEXT,
    created_at            TIMESTAMPTZ DEFAULT now(),
    UNIQUE (rule_version, cohort_rule_version, specification, outcome)
);

CREATE TABLE did_annual_effect (
    rule_version         TEXT NOT NULL,
    cohort_rule_version  TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    year                 SMALLINT NOT NULL,
    coefficient          NUMERIC(12,6),
    std_error            NUMERIC(12,6),
    t_stat               NUMERIC(12,6),
    n_treated            INTEGER,
    n_control            INTEGER,
    note                 TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (rule_version, cohort_rule_version, outcome, year)
);
CREATE INDEX idx_cohort_evidence_scholar ON cohort_evidence(scholar_id, rule_version);

-- ---------- Part 6b: A 轨不可变保护 ----------
CREATE OR REPLACE FUNCTION reject_a_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'baseline rule_version A_base_060 is immutable; use B track instead';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_event_a_immutable
BEFORE UPDATE OR DELETE ON mobility_event
FOR EACH ROW WHEN (OLD.rule_version = 'A_base_060')
EXECUTE FUNCTION reject_a_mutation();

CREATE TRIGGER trg_class_a_immutable
BEFORE UPDATE OR DELETE ON mobility_class
FOR EACH ROW WHEN (OLD.rule_version = 'A_base_060')
EXECUTE FUNCTION reject_a_mutation();

-- ---------- Part 6c: 权威判定视图（C1-C5 候选） ----------
CREATE OR REPLACE VIEW v_event_candidate AS
WITH p AS (
    SELECT ap.*,
           lag(primary_institution_id) OVER w AS prev_inst,
           lag(primary_country)        OVER w AS prev_country,
           lag(primary_is_tie)         OVER w AS prev_tie,
           lead(primary_country)       OVER w AS next_country
    FROM annual_panel ap
    WHERE ap.is_imputed = false
    WINDOW w AS (PARTITION BY scholar_id ORDER BY year)
)
SELECT scholar_id,
       year AS paper_year,
       prev_inst AS from_inst,
       primary_institution_id AS to_inst,
       prev_country AS from_country,
       primary_country AS to_country,
       next_country,
       CASE
           WHEN primary_country <> prev_country
                AND coalesce(prev_tie,false)=false
                AND coalesce(primary_is_tie,false)=false THEN 'C2'
           WHEN primary_institution_id <> prev_inst
                AND primary_country = prev_country
                AND coalesce(prev_tie,false)=false
                AND coalesce(primary_is_tie,false)=false THEN 'C1'
           WHEN NOT (secondary_countries @> ARRAY[primary_country])
                AND EXISTS (SELECT 1 FROM unnest(secondary_countries) s
                            WHERE s <> prev_country AND s <> primary_country) THEN 'C3'
           WHEN prev_country = ANY(secondary_countries) THEN 'C4'
           ELSE NULL
       END AS event_type
FROM p
WHERE primary_country IS DISTINCT FROM prev_country
   OR primary_institution_id IS DISTINCT FROM prev_inst
   OR cardinality(secondary_countries) > 0;

-- ---------- Part 7: Apache AGE 目标模式（仅说明，默认不执行） ----------
-- 这是目标设计（7 类顶点 / 14 种边）。三人小样实际只建 5 类顶点 / 9 种边，
-- 由 三人小样_AGE投影.sql 完成；两套标签定义不要同时执行。
-- 前置条件：已安装 AGE 扩展。若在 psql 中执行，先运行：
--   CREATE EXTENSION IF NOT EXISTS age;
--   LOAD 'age';
--   SET search_path = ag_catalog, "$user", public;

-- CREATE EXTENSION IF NOT EXISTS age;
-- LOAD 'age';
-- SET search_path = ag_catalog, "$user", public;

-- SELECT create_graph('mobility');
-- SELECT create_vlabel('mobility','Scholar');
-- SELECT create_vlabel('mobility','Institution');
-- SELECT create_vlabel('mobility','Country');
-- SELECT create_vlabel('mobility','Work');
-- SELECT create_vlabel('mobility','Event');
-- SELECT create_vlabel('mobility','Alias');
-- SELECT create_vlabel('mobility','CareerFact');
-- SELECT create_elabel('mobility','AFFILIATED');
-- SELECT create_elabel('mobility','AUTHORED');
-- SELECT create_elabel('mobility','SIGNED_AT');
-- SELECT create_elabel('mobility','LOCATED_IN');
-- SELECT create_elabel('mobility','EXPERIENCED');
-- SELECT create_elabel('mobility','FROM_INST');
-- SELECT create_elabel('mobility','TO_INST');
-- SELECT create_elabel('mobility','FROM_COUNTRY');
-- SELECT create_elabel('mobility','TO_COUNTRY');
-- SELECT create_elabel('mobility','CORROBORATED_BY');
-- SELECT create_elabel('mobility','RENAMED_TO');
-- SELECT create_elabel('mobility','IN_COHORT');

-- 图属性索引（语法随 AGE 版本变化，先 smoke test）
-- CREATE INDEX ON "mobility"."Scholar" USING btree ((properties->>'sid'));
-- CREATE INDEX ON "mobility"."Event"  USING btree ((properties->>'type'));
-- CREATE INDEX ON "mobility"."AFFILIATED" USING gin (properties);

-- 图投影 smoke test（10 行），验证通过后再跑全量
-- SELECT * FROM cypher('mobility', $$
--   CREATE (s:Scholar {sid:1, name:'smoke', orcid:'', openalex_id:'', birth_hint:'', h17:0})
--   RETURN s
-- $$) AS (v agtype);

-- ============================================================
-- END
-- ============================================================
