"""WP0 子集规模参数化离线测试：不读 Excel、不连数据库、不落盘。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wp0_import as w  # noqa: E402


@contextmanager
def tempdir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


class TestSubsetSize(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("OA_SUBSET_SIZE")
        os.environ.pop("OA_SUBSET_SIZE", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("OA_SUBSET_SIZE", None)
        else:
            os.environ["OA_SUBSET_SIZE"] = self._old

    def test_default_is_1000(self):
        self.assertEqual(w.subset_size(105026), 1000)

    def test_5000_is_honored(self):
        os.environ["OA_SUBSET_SIZE"] = "5000"
        self.assertEqual(w.subset_size(105026), 5000)

    def test_clamped_to_available_rows(self):
        os.environ["OA_SUBSET_SIZE"] = "200000"
        self.assertEqual(w.subset_size(105026), 105026)

    def test_invalid_and_nonpositive_fall_back(self):
        for bad in ("abc", "0", "-5"):
            with self.subTest(bad=bad):
                os.environ["OA_SUBSET_SIZE"] = bad
                got = w.subset_size(105026)
                self.assertIn(got, (1, 1000))

    def test_main_uses_env_for_subset_and_sql_comment(self):
        """锁住端到端参数化：main() 写出的子集与 SQL 头注释都反映 OA_SUBSET_SIZE。"""
        os.environ["OA_SUBSET_SIZE"] = "5000"
        rows = [
            {
                "excel_row": i, "source_row": i - 1, "authfull": "n%d" % i,
                "inst_name": "inst", "cntry": "usa", "np6017": "10",
                "firstyr": "2000", "lastyr": "2020", "h17": "5",
                "citations": "100", "discipline": "d", "raw_payload": {},
            }
            for i in range(1, 105027)
        ]
        with tempdir() as td:
            with mock.patch.object(w, "load_rows", return_value=rows), \
                 mock.patch.object(w, "batch_dir", return_value=Path(td)):
                w.main()
            sub = (Path(td) / "subset.jsonl").read_text(encoding="utf-8").strip().splitlines()
            sql_head = (Path(td) / "subset_source.sql").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(len(sub), 5000)
        self.assertIn("前 5000 数据行", sql_head)


if __name__ == "__main__":
    unittest.main()
