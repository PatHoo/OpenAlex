"""WP15 外部证据规则的单元测试：不联网、不连库。

锁定：来源分级、身份锚点优先、四档 verdict、treated 白名单、
种子每条可核查（URL 绝对、有原文摘录、claim 合法）。
"""
from __future__ import annotations

import unittest

from external_evidence_rules import (
    CLAIM_BIRTH_PLACE_CN,
    CLAIM_EDUCATION_CN,
    CLAIM_IDENTITY_MISMATCH,
    CLAIM_NO_CN_IN_PUBLIC_CV,
    EXTERNAL_RULE_VERSION,
    EvidenceItem,
    decide_verdict,
    is_treated,
)
from external_evidence_seed import SUBJECTS, all_items

GOOD_URL = "https://example.org/profile"


def _item(claim: str, source: str = "official", url: str = GOOD_URL) -> EvidenceItem:
    return EvidenceItem(
        scholar_id=1, source_type=source, source_url=url,
        source_title="t", quote_text="some public quote", claim=claim,
        birth_country="CN" if claim == CLAIM_BIRTH_PLACE_CN else None,
    )


class ExternalRuleTest(unittest.TestCase):
    def test_official_birth_place_is_verified(self) -> None:
        self.assertEqual(
            decide_verdict("matched", [_item(CLAIM_BIRTH_PLACE_CN)]),
            "verified_cn_birth",
        )

    def test_two_independent_birth_sources_plus_education_is_verified(self) -> None:
        items = [
            _item(CLAIM_BIRTH_PLACE_CN, "encyclopedia", "https://a.example/1"),
            _item(CLAIM_BIRTH_PLACE_CN, "encyclopedia", "https://b.example/2"),
            _item(CLAIM_EDUCATION_CN),
        ]
        self.assertEqual(decide_verdict("matched", items), "verified_cn_birth")

    def test_duplicate_url_does_not_count_as_independent(self) -> None:
        items = [
            _item(CLAIM_BIRTH_PLACE_CN, "encyclopedia", GOOD_URL),
            _item(CLAIM_BIRTH_PLACE_CN, "encyclopedia", GOOD_URL),
            _item(CLAIM_EDUCATION_CN),
        ]
        # 只有 1 个独立 URL 且非官方 → 降到 probable
        self.assertEqual(decide_verdict("matched", items), "probable_cn_birth")

    def test_education_only_is_probable(self) -> None:
        self.assertEqual(
            decide_verdict("matched", [_item(CLAIM_EDUCATION_CN)]),
            "probable_cn_birth",
        )

    def test_full_cv_without_cn_is_not_supported(self) -> None:
        self.assertEqual(
            decide_verdict("matched", [_item(CLAIM_NO_CN_IN_PUBLIC_CV)]),
            "not_supported",
        )

    def test_unmatched_identity_always_unresolved(self) -> None:
        self.assertEqual(
            decide_verdict("unmatched", [_item(CLAIM_BIRTH_PLACE_CN)]),
            "identity_unresolved",
        )

    def test_identity_mismatch_blocks_even_with_birth_claim(self) -> None:
        items = [_item(CLAIM_BIRTH_PLACE_CN), _item(CLAIM_IDENTITY_MISMATCH)]
        self.assertEqual(decide_verdict("matched", items), "identity_unresolved")

    def test_treated_whitelist(self) -> None:
        self.assertTrue(is_treated("verified_cn_birth"))
        self.assertTrue(is_treated("probable_cn_birth"))
        self.assertFalse(is_treated("not_supported"))
        self.assertFalse(is_treated("identity_unresolved"))

    def test_invalid_evidence_rejected(self) -> None:
        # birth_place_cn 主张必须带 birth_country='CN'
        bad = EvidenceItem(
            scholar_id=1, source_type="official", source_url=GOOD_URL,
            source_title="t", quote_text="q", claim=CLAIM_BIRTH_PLACE_CN,
            birth_country=None,
        )
        with self.assertRaises(ValueError):
            bad.validate()
        with self.assertRaises(ValueError):
            EvidenceItem(
                scholar_id=1, source_type="blog", source_url=GOOD_URL,
                source_title="t", quote_text="q", claim=CLAIM_EDUCATION_CN,
            ).validate()
        with self.assertRaises(ValueError):
            EvidenceItem(
                scholar_id=1, source_type="official", source_url="not-a-url",
                source_title="t", quote_text="q", claim=CLAIM_EDUCATION_CN,
            ).validate()


class SeedAuditTest(unittest.TestCase):
    def test_all_seed_items_validate(self) -> None:
        items = all_items()  # validate() 在内部调用，抛错即失败
        self.assertGreater(len(items), 10)

    def test_subjects_cover_seven_high_candidates(self) -> None:
        self.assertEqual(len(SUBJECTS), 7)
        self.assertEqual(
            sorted(s.scholar_id for s in SUBJECTS),
            [114, 121, 266, 326, 358, 802, 840],
        )

    def test_false_positive_and_unresolved_are_excluded(self) -> None:
        by_id = {s.scholar_id: s for s in SUBJECTS}
        wang = decide_verdict(by_id[114].identity_check, list(by_id[114].items))
        liu = decide_verdict(by_id[358].identity_check, list(by_id[358].items))
        self.assertEqual(wang, "not_supported")
        self.assertEqual(liu, "identity_unresolved")
        self.assertFalse(is_treated(wang))
        self.assertFalse(is_treated(liu))

    def test_exactly_five_eligible_treated(self) -> None:
        eligible = [
            s.scholar_id for s in SUBJECTS
            if is_treated(decide_verdict(s.identity_check, list(s.items)))
        ]
        self.assertEqual(sorted(eligible), [121, 266, 326, 802, 840])

    def test_every_evidence_row_has_retrievable_url_and_quote(self) -> None:
        for s in SUBJECTS:
            for it in s.items:
                self.assertTrue(it.source_url.startswith("https://") or
                                it.source_url.startswith("http://"))
                self.assertGreaterEqual(len(it.quote_text.strip()), 10)
                self.assertTrue(it.source_title.strip())


class ExternalRuleVersionTest(unittest.TestCase):
    def test_rule_version_is_stable(self) -> None:
        self.assertEqual(EXTERNAL_RULE_VERSION, "EXTERNAL_BIRTH_v1")


if __name__ == "__main__":
    unittest.main()
