"""回归测试：锁定 WP11 的 8.5 分类口径（纯静态，不连库不联网）。

运行：cd pipeline && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "pipeline"
sys.path.insert(0, str(PIPELINE))

import wp11_classify as wp11  # noqa: E402

ALL_COMBOS = {(d, i, s) for d in (0, 1) for i in (0, 1) for s in (0, 1)}
EXPECTED = {  # 8.5.2 权威表：D/I/S -> M1..M8
    (0, 0, 0): "M1", (1, 0, 0): "M2", (0, 1, 0): "M3", (0, 0, 1): "M4",
    (1, 1, 0): "M5", (1, 0, 1): "M6", (0, 1, 1): "M7", (1, 1, 1): "M8",
}


class TestClassRuleSeed(unittest.TestCase):
    """design/class_rule_85_seed.sql 必须是 8.5.2 全组合，一条不少。"""

    def test_seed_has_all_eight_combinations(self):
        seed = (ROOT / "design" / "class_rule_85_seed.sql").read_text(encoding="utf-8")
        found = {}
        for d, i, s, code in __import__("re").findall(
            r"\((\d),(\d),(\d),'(M[1-8])'", seed
        ):
            found[(int(d), int(i), int(s))] = code
        self.assertEqual(set(found), ALL_COMBOS, "class_rule seed 未覆盖 D/I/S 全 8 组合")
        self.assertEqual(found, EXPECTED, "class_rule seed 的 D/I/S→M 映射与 8.5.2 不符")


class TestWP11OralSpec(unittest.TestCase):
    """wp11 SQL 必须体现 8.5 的关键口径，防止被改回宽松判定。"""

    def test_di_only_count_confirmed_probable_unsuppressed(self):
        # D/I 的 1 必须排除 Possible 与被抑制的 C1/C2
        self.assertIn("evidence_level IN ('Confirmed','Probable')", wp11.BUILD_SQL)
        self.assertIn("NOT is_suppressed", wp11.BUILD_SQL)

    def test_possible_candidate_gates_formal_class(self):
        # 阴性维度存在 Possible 未决候选 -> formal_class 留 NULL
        self.assertIn("f.d_undecided OR f.i_undecided", wp11.BUILD_SQL)
        self.assertIn("THEN NULL ELSE cr.class_code END", wp11.BUILD_SQL)

    def test_s_uses_panel_secondary_countries(self):
        self.assertIn("cardinality(secondary_countries)>0", wp11.BUILD_SQL)

    def test_counts_are_separate_view(self):
        # 次数与类型分开：独立视图，不改 mobility_class 表
        self.assertIn("CREATE OR REPLACE VIEW v_mobility_counts", wp11.BUILD_SQL)

    def test_rule_version_isolated(self):
        self.assertEqual(wp11.RV, "A_1000_v1")
        self.assertIn("DELETE FROM mobility_class WHERE rule_version=", wp11.BUILD_SQL)


if __name__ == "__main__":
    unittest.main()
