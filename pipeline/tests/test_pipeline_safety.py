"""回归测试：锁定 P0 "静默丢数" 修复，防止以后改回去。

运行：
    cd pipeline && python -m unittest discover -s tests -v

只用标准库 unittest + mock，不访问 OpenAlex，也不访问数据库。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import wp12_fetch  # noqa: E402
import wp5_import_authorship as wp5  # noqa: E402


class TestWP12RequestFailure(unittest.TestCase):
    """client.get 返回 None（网络重试耗尽/4xx/key 未激活）必须中止，不能当正常结果。"""

    def test_wp1_failure_raises_instead_of_empty_candidates(self):
        client = mock.Mock()
        client.get.return_value = None  # 请求失败
        with self.assertRaises(wp12_fetch.APIFetchFailed):
            wp12_fetch.wp1_search(client, {"src_id": 42, "authfull": "Smith, J"}, 5)

    def test_wp2_failure_raises_instead_of_returning_partial_works(self):
        client = mock.Mock()
        client.get.side_effect = [
            {"results": [{"id": "https://openalex.org/W1"}], "meta": {"next_cursor": "cur2"}},
            None,  # 第二页请求失败
        ]
        # 修复前：返回 1 条半截 works，主循环会落盘并标记 wp2=True → 永久丢数
        with self.assertRaises(wp12_fetch.APIFetchFailed):
            wp12_fetch.wp2_fetch_works(client, {"src_id": 42}, "https://openalex.org/A1", None)

    def test_wp2_normal_end_still_returns_all_works(self):
        client = mock.Mock()
        client.get.side_effect = [
            {"results": [{"id": "W1"}], "meta": {"next_cursor": "cur2"}},
            {"results": [{"id": "W2"}], "meta": {"next_cursor": None}},
        ]
        got = wp12_fetch.wp2_fetch_works(client, {"src_id": 1}, "A1", None)
        self.assertEqual([r["work"]["id"] for r in got], ["W1", "W2"])

    def test_main_does_not_mark_scholar_complete_on_wp1_failure(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "subset.jsonl").write_text(
                json.dumps({"src_id": 42, "authfull": "Smith, J"}) + "\n", encoding="utf-8"
            )
            (d / "checkpoint.json").write_text("{}", encoding="utf-8")
            client = mock.Mock()
            client.key = "test-key"
            client.get.return_value = None
            client.request_count = 0
            with mock.patch.object(wp12_fetch, "batch_dir", return_value=d), \
                 mock.patch.object(wp12_fetch, "OpenAlexClient", return_value=client), \
                 mock.patch.object(sys, "argv", ["wp12_fetch.py", "--limit", "1"]):
                wp12_fetch.main()
            cp = json.loads((d / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertFalse(cp.get("42", {}).get("wp1"), "请求失败不得标记 wp1 完成")
            self.assertFalse((d / "candidates.jsonl").exists(), "请求失败不得写入空候选")


class TestWP5CopyFailure(unittest.TestCase):
    """COPY 失败时 offset 不得推进、staging 不得清空；成功后才推进。"""

    def _make_data_dir(self, d: Path):
        (d / "_scholar_map.txt").write_text("42|https://openalex.org/A1|7\n", encoding="utf-8")
        (d / "_work_map.txt").write_text(
            "100|https://openalex.org/W1\n", encoding="utf-8"
        )
        (d / "source_scholar.jsonl").write_text(
            json.dumps({"src_id": 42, "authfull": "Smith, J"}) + "\n", encoding="utf-8"
        )
        work = {
            "src_id": 42,
            "work": {
                "id": "https://openalex.org/W1",
                "authorships": [{
                    "author": {"id": "https://openalex.org/A1"},
                    "author_position": "first",
                    "raw_author_name": "Smith, J",
                    "raw_affiliation_strings": ["Test University"],
                }],
            },
        }
        (d / "works.jsonl").write_text(json.dumps(work) + "\n", encoding="utf-8")
        (d / "authorship_progress.json").write_text(
            json.dumps({"offset": 0, "scanned": 0, "rows": 0,
                        "skip_unsolved": 0, "skip_no_auth": 0, "skip_no_work": 0}),
            encoding="utf-8",
        )

    def test_copy_failure_keeps_offset_and_staging(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._make_data_dir(d)
            # psql 调用已收敛到 dbutil.run_psql；失败以 RuntimeError 抛出
            with mock.patch.object(wp5, "DATA", d), \
                 mock.patch.object(wp5, "run_psql",
                                   side_effect=RuntimeError("ERROR: copy failed")), \
                 mock.patch.object(sys, "argv", ["wp5.py"]):
                with self.assertRaises(SystemExit) as cm:
                    wp5.main()
            self.assertEqual(cm.exception.code, 1)
            state = json.loads((d / "authorship_progress.json").read_text(encoding="utf-8"))
            self.assertEqual(state["offset"], 0, "COPY 失败不得推进 offset")
            self.assertGreater((d / "_authorship_stage.csv").stat().st_size, 0,
                               "COPY 失败必须保留 staging 供重跑")

    def test_copy_success_advances_offset_and_clears_staging(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._make_data_dir(d)
            with mock.patch.object(wp5, "DATA", d), \
                 mock.patch.object(wp5, "run_psql", return_value="INSERT 0 1"), \
                 mock.patch.object(sys, "argv", ["wp5.py"]):
                wp5.main()
            state = json.loads((d / "authorship_progress.json").read_text(encoding="utf-8"))
            self.assertEqual(state["offset"], 1)
            self.assertEqual((d / "_authorship_stage.csv").stat().st_size, 0)

    def test_dry_run_does_not_touch_official_staging(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._make_data_dir(d)
            (d / "_authorship_stage.csv").write_text("SENTINEL\n", encoding="utf-8")
            with mock.patch.object(wp5, "DATA", d), \
                 mock.patch.object(sys, "argv", ["wp5.py", "--dry-run", "--limit", "1"]):
                wp5.main()
            self.assertEqual((d / "_authorship_stage.csv").read_text(encoding="utf-8"), "SENTINEL\n")
            self.assertTrue((d / "_authorship_stage_dryrun.csv").exists())


if __name__ == "__main__":
    unittest.main()
