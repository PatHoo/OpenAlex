"""export_local_tools 空结果降级：写带表头的 CSV，不中断整批导出。"""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import export_local_tools as e


class ExportCsvTest(unittest.TestCase):
    def test_empty_result_writes_header_only(self):
        # 机构经纬度在试跑数据里全空，institution_points.csv 会是 0 行。
        class Col:
            def __init__(self, name):
                self.name = name

        cur = mock.MagicMock()
        cur.description = [Col("institution_id"), Col("canonical_name"), Col("lat"), Col("lon")]
        cur.fetchall.return_value = []
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = mock.Mock(return_value=cur)
        conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            with mock.patch.object(e, "DATA", out):
                e.export_csv(conn, "institution_points.csv", "SELECT ...")
            path = out / "institution_points.csv"
            self.assertTrue(path.exists())
            with path.open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows, [])
            with path.open(encoding="utf-8-sig") as fh:
                self.assertIn("institution_id", fh.readline())

    def test_rows_still_exported(self):
        cur = mock.MagicMock()
        cur.description = [mock.Mock(name="x")]  # 用不到，有 rows 时走 rows[0].keys()
        cur.description[0].name = "id"
        cur.fetchall.return_value = [{"id": 1}, {"id": 2}]
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = mock.Mock(return_value=cur)
        conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            with mock.patch.object(e, "DATA", out):
                e.export_csv(conn, "x.csv", "SELECT ...")
            with (out / "x.csv").open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows, [{"id": "1"}, {"id": "2"}])


if __name__ == "__main__":
    unittest.main()
