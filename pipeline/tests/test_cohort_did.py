"""WP13/WP14 静态回归：不联网、不连库，锁定队列与 DID 口径。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from cohort_rules import (
    GREATER_CHINA_CODES,
    STRONG_PINYIN_SURNAMES,
    WEAK_PINYIN_SURNAMES,
    classify_confidence,
    regex_for_surnames,
)

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "pipeline"
DESIGN = ROOT / "design"


class CohortRuleTest(unittest.TestCase):
    def test_strong_pinyin_regex_matches_token_not_embedded(self) -> None:
        rx = re.compile(regex_for_surnames(("wang", "liu")))
        self.assertIsNotNone(rx.search("wang jie"))
        self.assertIsNotNone(rx.search("liu, jie"))  # 非字母边界
        self.assertIsNone(rx.search("wanger jie"))

    def test_weak_romanization_regex(self) -> None:
        rx = re.compile(regex_for_surnames(WEAK_PINYIN_SURNAMES))
        self.assertIsNotNone(rx.search("chou kuo-chen"))
        self.assertIsNotNone(rx.search("hsu frank"))

    def test_greater_china_codes_include_hk_tw_regions(self) -> None:
        self.assertEqual(GREATER_CHINA_CODES, ("CN", "HK", "TW"))

    def test_high_requires_exact_2017_non_tie(self) -> None:
        base = dict(
            strong_pinyin=True,
            weak_pinyin=False,
            greater_china_signal=True,
            birth_hint=False,
            us_basis="exact2017",
            us_primary_is_tie=False,
        )
        self.assertEqual(classify_confidence(**base), "high")
        self.assertEqual(classify_confidence(**{**base, "us_basis": "fallback2014_2017"}), "medium")
        self.assertEqual(classify_confidence(**{**base, "us_primary_is_tie": True}), "medium")
        self.assertIsNone(classify_confidence(**{**base, "us_basis": None}))

    def test_birth_hint_high_even_without_pinyin(self) -> None:
        self.assertEqual(
            classify_confidence(
                strong_pinyin=False,
                weak_pinyin=False,
                greater_china_signal=False,
                birth_hint=True,
                us_basis="exact2017",
                us_primary_is_tie=False,
            ),
            "high",
        )

    def test_low_when_only_name_or_only_china_signal(self) -> None:
        common = dict(birth_hint=False, us_basis="exact2017", us_primary_is_tie=False)
        self.assertEqual(
            classify_confidence(
                strong_pinyin=False, weak_pinyin=True,
                greater_china_signal=False, **common
            ),
            "low",
        )
        self.assertEqual(
            classify_confidence(
                strong_pinyin=False, weak_pinyin=False,
                greater_china_signal=True, **common
            ),
            "low",
        )


class CohortSqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = (PIPELINE / "wp13_cohort_screen.py").read_text(encoding="utf-8")

    def test_high_confidence_uses_greater_china_signal_and_exact_2017(self) -> None:
        self.assertIn("c.strong_surname IS NOT NULL AND c.gc_any", self.sql)
        self.assertIn("c.us_basis='exact2017' AND NOT coalesce(c.primary_is_tie,false)", self.sql)

    def test_birth_country_not_asserted_by_algorithm(self) -> None:
        self.assertIn("CASE WHEN c.birth_hint THEN c.birth_country_hint::char(2) ELSE NULL END", self.sql)
        self.assertIn("algorithm_inference_v1", self.sql)
        self.assertIn("birth_country not asserted", self.sql)

    def test_reviewed_rows_are_preserved(self) -> None:
        self.assertIn("WHERE reviewed_by IS NULL", self.sql)
        self.assertIn("WHERE cohort_member.reviewed_by IS NULL", self.sql)


class DidSqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = (PIPELINE / "wp14_did.py").read_text(encoding="utf-8")

    def test_treated_is_external_evidence_eligible_only(self) -> None:
        # v2：treated 从算法 high 改为 WP15 外部证据定案（5 人）
        self.assertIn("cohort_external_review", self.sql)
        self.assertIn("coalesce(cr.eligible_treated, false) AS treated", self.sql)
        self.assertIn("WHERE cm.scholar_id IS NULL OR coalesce(cr.eligible_treated, false)", self.sql)
        self.assertIn("EXTERNAL_RULE_VERSION", self.sql)

    def test_post_and_window_are_2018_and_2014_2021(self) -> None:
        self.assertIn("year >= 2018", self.sql)
        self.assertIn("BETWEEN 2014 AND 2021", self.sql)

    def test_events_limited_to_accepted_versions_and_levels(self) -> None:
        self.assertIn("WORK_RULE_VERSION", self.sql)
        self.assertIn("WHERE rule_version='{WRV}'", self.sql)
        self.assertIn("evidence_level IN ('Confirmed','Probable')", self.sql)
        self.assertIn("NOT is_suppressed", self.sql)

    def test_parallel_trend_and_uncertainty_are_explicit(self) -> None:
        self.assertIn("abs(t_stat) < 1.96", self.sql)
        self.assertIn("independent-cell SE, descriptive only", self.sql)
        self.assertIn("event study vs {BASELINE_YEAR}", self.sql)

    def test_zero_variance_cells_do_not_divide_by_zero(self) -> None:
        # 重跑数据里 treated 组 cn_link 全 0 -> sd=0 -> se=0；
        # 直接 coef/se 会报 division by zero（2026-11-24 实测）
        self.assertIn("coef/NULLIF(se,0)", self.sql)
        self.assertIn("d.coef/NULLIF(d.se,0)", self.sql)


class MigrationTest(unittest.TestCase):
    def test_migration_creates_audit_tables_and_did_columns(self) -> None:
        sql = (DESIGN / "cohort_did_seed.sql").read_text(encoding="utf-8")
        for token in (
            "CREATE TABLE IF NOT EXISTS cohort_evidence",
            "CREATE TABLE IF NOT EXISTS did_estimate",
            "CREATE TABLE IF NOT EXISTS did_annual_effect",
            "ADD COLUMN IF NOT EXISTS outcome_intl_moves",
            "ADD COLUMN IF NOT EXISTS outcome_cn_link",
            "ADD COLUMN IF NOT EXISTS outcome_log_works",
        ):
            self.assertIn(token, sql)


if __name__ == "__main__":
    unittest.main()
