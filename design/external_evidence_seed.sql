-- WP15 增量迁移：外部出生国证据（公开来源逐条留痕）+ 人工复核定案表
-- 可重复执行；不改写 WP13 算法候选表，避免把"推断"当"事实"。
BEGIN;

CREATE TABLE IF NOT EXISTS external_birth_evidence (
    evidence_id     BIGSERIAL PRIMARY KEY,
    scholar_id      BIGINT NOT NULL REFERENCES scholar(scholar_id),
    rule_version    TEXT NOT NULL,
    source_type     TEXT NOT NULL CHECK (source_type IN ('official','encyclopedia','news')),
    source_url      TEXT NOT NULL,
    source_title    TEXT NOT NULL,
    quote_text      TEXT NOT NULL,
    claim           TEXT NOT NULL CHECK (claim IN
                      ('birth_place_cn','education_cn','nationality_cn',
                       'career_cn_early','no_cn_in_public_cv','identity_mismatch')),
    birth_country   CHAR(2),
    weight          SMALLINT NOT NULL DEFAULT 0,
    identity_check  TEXT NOT NULL CHECK (identity_check IN ('matched','partial','unmatched')),
    collected_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (scholar_id, rule_version, source_url, claim)
);
CREATE INDEX IF NOT EXISTS idx_ext_birth_evidence_scholar
    ON external_birth_evidence(scholar_id, rule_version);

CREATE TABLE IF NOT EXISTS cohort_external_review (
    scholar_id          BIGINT PRIMARY KEY REFERENCES scholar(scholar_id),
    rule_version        TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    orcid               TEXT,
    identity_check      TEXT NOT NULL CHECK (identity_check IN ('matched','partial','unmatched')),
    identity_note       TEXT NOT NULL,
    verdict             TEXT NOT NULL CHECK (verdict IN
                          ('verified_cn_birth','probable_cn_birth',
                           'not_supported','identity_unresolved')),
    eligible_treated    BOOLEAN NOT NULL,
    source_count        INTEGER NOT NULL,
    birth_country       CHAR(2),
    review_note         TEXT NOT NULL,
    reviewed_at         TIMESTAMPTZ DEFAULT now()
);

COMMIT;
