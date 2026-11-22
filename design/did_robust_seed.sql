-- WP16 增量迁移：聚类稳健 DID 估计与逐年效应（与独立组-期近似分开存放）
-- 可重复执行。rule_version 与 wp14 的 DID 版本一致，靠 specification 区分。
BEGIN;

CREATE TABLE IF NOT EXISTS did_robust_estimate (
    estimate_id          BIGSERIAL PRIMARY KEY,
    rule_version         TEXT NOT NULL,
    cohort_rule_version  TEXT NOT NULL,
    specification        TEXT NOT NULL CHECK (specification IN ('did_2x2_cluster','event_study_cluster')),
    outcome              TEXT NOT NULL,
    coefficient          NUMERIC(14,6),
    std_error            NUMERIC(14,6),
    t_stat               NUMERIC(14,6),
    p_value              NUMERIC(14,6),
    ci_low               NUMERIC(14,6),
    ci_high              NUMERIC(14,6),
    n_obs                INTEGER,
    n_scholars           INTEGER,
    n_clusters           INTEGER,
    n_treated            INTEGER,
    n_control            INTEGER,
    r_squared            NUMERIC(10,6),
    joint_f_stat         NUMERIC(14,6),
    joint_df1            INTEGER,
    joint_df2            INTEGER,
    joint_p_value        NUMERIC(14,6),
    parallel_trend_passed BOOLEAN,
    note                 TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    UNIQUE (rule_version, cohort_rule_version, specification, outcome)
);

CREATE TABLE IF NOT EXISTS did_robust_annual_effect (
    rule_version         TEXT NOT NULL,
    cohort_rule_version  TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    year                 SMALLINT NOT NULL,
    lead_or_lag          TEXT NOT NULL CHECK (lead_or_lag IN ('lead','baseline','lag')),
    coefficient          NUMERIC(14,6),
    std_error            NUMERIC(14,6),
    t_stat               NUMERIC(14,6),
    p_value              NUMERIC(14,6),
    ci_low               NUMERIC(14,6),
    ci_high              NUMERIC(14,6),
    note                 TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (rule_version, cohort_rule_version, outcome, year)
);

COMMIT;
