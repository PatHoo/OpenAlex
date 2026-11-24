"""rerun_1000.py 的离线回归测试：不访问 API、不连数据库。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rerun_1000 as r  # noqa: E402


class TestCostLedger(unittest.TestCase):
    def test_reconstruct_from_requests_log_and_no_double_count(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            log = p / "requests.jsonl"
            rows = [
                {"http_status": 200, "purpose": "wp1_author_search"},
                {"http_status": 200, "purpose": "wp2_works"},
                {"http_status": 200, "purpose": "wp2_works"},
                {"http_status": 429, "purpose": "wp2_works"},       # 不计费
                {"http_status": 200, "purpose": "rate_limit_check"},  # 免费
                {"http_status": 200, "purpose": "unknown_purpose"},   # 未定义单价
            ]
            log.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
            led = r.CostLedger.load(p / "cost_ledger.json", log, 2.20)
            self.assertEqual(led.counts["wp1_author_search"], 1)
            self.assertEqual(led.counts["wp2_works"], 2)
            self.assertEqual(led.counts["rate_limit_check"], 1)
            self.assertAlmostEqual(led.spent, 0.001 + 0.0002)
            again = r.CostLedger.load(p / "cost_ledger.json", log, 2.20)
            self.assertAlmostEqual(again.spent, led.spent)

    def test_cap_is_checked_before_charge(self):
        with tempfile.TemporaryDirectory() as td:
            led = r.CostLedger(Path(td) / "ledger.json", cap=0.0015)
            led.check("wp1_author_search")  # 通过
            led.charge("wp1_author_search")
            self.assertAlmostEqual(led.spent, 0.001)
            with self.assertRaises(r.CostCapExceeded):
                led.check("wp1_author_search")
            self.assertAlmostEqual(led.spent, 0.001)  # 未发生第二次扣费

    def test_free_endpoint_never_charges(self):
        with tempfile.TemporaryDirectory() as td:
            led = r.CostLedger(Path(td) / "ledger.json", cap=0.0)
            led.check("rate_limit_check")
            led.charge("rate_limit_check")
            self.assertEqual(led.spent, 0.0)


class TestKeys(unittest.TestCase):
    @staticmethod
    def _clean_key_env(extra: dict[str, str]) -> dict[str, str]:
        """清掉所有 OPENALEX_API_KEY* 变量，避免真实凭据污染测试。

        load_keys 中编号 key（KEY_1..8）优先于逗号分隔的 OPENALEX_API_KEYS，
        若 shell 里导出了真实 key，fallback 分支将永远走不到。
        """
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("OPENALEX_API_KEY")}
        for i in range(1, 9):
            env[f"OPENALEX_API_KEY_{i}"] = ""
        env["OPENALEX_API_KEYS"] = ""
        env.update(extra)
        return env

    def test_numbered_keys_dedupe_and_env_restored(self):
        env = self._clean_key_env({
            "OPENALEX_API_KEY_1": "abcd1234efgh",
            "OPENALEX_API_KEY_2": "abcd1234efgh",  # 重复，应去掉
            "OPENALEX_API_KEY_3": "wxyz9876klmn",
        })
        with mock.patch.dict(os.environ, env, clear=True):
            keys = r.load_keys()
        self.assertEqual([k for _, k in keys],
                         ["abcd1234efgh", "wxyz9876klmn"])
        self.assertEqual(r.mask_key("abcd1234efgh"), "abcd****efgh")

    def test_comma_separated_fallback(self):
        env = self._clean_key_env({"OPENALEX_API_KEYS": " keyA , , keyB "})
        with mock.patch.dict(os.environ, env, clear=True):
            keys = r.load_keys()
        self.assertEqual([k for _, k in keys], ["keyA", "keyB"])


class TestBudgetedClient(unittest.TestCase):
    def test_blocked_request_never_reaches_network(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            led = r.CostLedger(p / "ledger.json", cap=0.0015)
            with mock.patch.object(r.OpenAlexClient, "get",
                                   return_value={"meta": {}}) as net:
                client = r.BudgetedClient("key123456789", p / "req.jsonl", "b", led)
                self.assertIsNotNone(
                    client.get("authors", purpose="wp1_author_search"))
                self.assertEqual(net.call_count, 1)
                self.assertAlmostEqual(led.spent, 0.001)
                with self.assertRaises(r.CostCapExceeded):
                    client.get("authors", purpose="wp1_author_search")
                self.assertEqual(net.call_count, 1)  # 关键：超限请求没发出去

    def test_short_key_does_not_leak_into_env(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            led = r.CostLedger(p / "ledger.json", cap=1.0)
            with mock.patch.dict(os.environ, {}, clear=True):
                r.BudgetedClient("key123456789", p / "req.jsonl", "b", led)
                self.assertNotIn("OPENALEX_API_KEY", os.environ)


class TestDBSteps(unittest.TestCase):
    def test_source_scholar_import_precedes_load_decisions(self):
        # 空库跳过 subset_source.sql 会触发 scholar_src_id_fkey（2026-11-24 实测）
        kinds = {name: kind for kind, name in r.DB_STEPS}
        names = [n for _, n in r.DB_STEPS]
        self.assertEqual(kinds.get("subset_source.sql"), "batchsql")
        self.assertLess(names.index("subset_source.sql"),
                        names.index("wp4_load_decisions.py"))

    def test_seed_order_matches_readme(self):
        names = [n for _, n in r.DB_STEPS]
        self.assertLess(names.index("class_rule_85_seed.sql"),
                        names.index("wp11_classify.py"))
        self.assertLess(names.index("did_robust_seed.sql"),
                        names.index("wp13_cohort_screen.py"))
        self.assertLess(names.index("wp16_did_robust.py"),
                        names.index("export_platform_snapshot.py"))

    def test_export_mapping_files_skips_when_both_exist(self):
        # 已存在的映射文件不应被覆盖（续跑时保留现场）
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "_scholar_map.txt").write_text("2|A1|2\n", encoding="utf-8")
            (p / "_work_map.txt").write_text("1|W1\n", encoding="utf-8")
            with mock.patch.object(r, "batch_dir", return_value=p), \
                 mock.patch("dbutil.query_psql") as q:
                r.export_mapping_files("batch_test")
            q.assert_not_called()
            self.assertEqual((p / "_scholar_map.txt").read_text(encoding="utf-8"),
                             "2|A1|2\n")

    def test_export_mapping_files_rebuilds_missing(self):
        # 缺失时应从库重建，格式为 管道分隔、每行一条
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "_scholar_map.txt").write_text("2|A1|2\n", encoding="utf-8")
            with mock.patch.object(r, "batch_dir", return_value=p), \
                 mock.patch("dbutil.query_psql", return_value="1|https://x/W1\n") as q:
                r.export_mapping_files("batch_test")
            self.assertEqual(q.call_count, 1)  # scholar 已存在，只查 work
            self.assertEqual((p / "_work_map.txt").read_text(encoding="utf-8"),
                             "1|https://x/W1\n")


if __name__ == "__main__":
    unittest.main()
