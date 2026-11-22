-- WP13/WP14 增量迁移：中国背景候选队列证据 + DID 面板/估计落库
-- 可重复执行；不改动三人小样 A 轨既有数据。
BEGIN;

CREATE TABLE IF NOT EXISTS cohort_evidence (
    evidence_id    BIGSERIAL PRIMARY KEY,
    scholar_id     BIGINT NOT NULL REFERENCES scholar(scholar_id),
    rule_version   TEXT NOT NULL,
    signal_code    TEXT NOT NULL CHECK (signal_code IN
                   ('pinyin_strong','pinyin_weak','gc_primary','gc_secondary',
                    'gc_share','gc_cv_verified','birth_hint',
                    'us_2017_primary','us_fallback')),
    signal_value   TEXT,
    evidence_detail TEXT,
    source_table   TEXT,
    source_key     TEXT NOT NULL DEFAULT '',
    weight         SMALLINT NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (scholar_id, rule_version, signal_code, source_key)
);
CREATE INDEX IF NOT EXISTS idx_cohort_evidence_scholar
    ON cohort_evidence(scholar_id, rule_version);

CREATE TABLE IF NOT EXISTS did_estimate (
    estimate_id          BIGSERIAL PRIMARY KEY,
    rule_version         TEXT NOT NULL,
    cohort_rule_version  TEXT NOT NULL,
    specification        TEXT NOT NULL CHECK (specification IN ('did_2x2','event_study')),
    outcome              TEXT NOT NULL,
    coefficient          NUMERIC(12,6),
    std_error            NUMERIC(12,6),
    t_stat               NUMERIC(12,6),
    n_treated            INTEGER,
    n_control            INTEGER,
    n_obs                INTEGER,
    pretrend_coef        NUMERIC(12,6),
    parallel_trend_passed BOOLEAN,
    note                 TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    UNIQUE (rule_version, cohort_rule_version, specification, outcome)
);

CREATE TABLE IF NOT EXISTS did_annual_effect (
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

-- did_panel 在初始 schema 中只有 moves 一个结果列；补上四个可核查结果变量。
-- 一次只保留一个活动 DID 版本，版本写入 did_estimate/did_annual_effect。
ALTER TABLE did_panel ADD COLUMN IF NOT EXISTS outcome_intl_moves INTEGER DEFAULT 0;
ALTER TABLE did_panel ADD COLUMN IF NOT EXISTS outcome_cn_link INTEGER DEFAULT 0;
ALTER TABLE did_panel ADD COLUMN IF NOT EXISTS outcome_log_works NUMERIC(10,4);
ALTER TABLE did_panel ADD COLUMN IF NOT EXISTS cohort_confidence TEXT
    CHECK (cohort_confidence IN ('high','medium','low'));

COMMIT;
