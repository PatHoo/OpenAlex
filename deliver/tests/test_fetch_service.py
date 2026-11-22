"""fetch_service 抓取服务层的单元测试（不联网、不连库）。"""
from __future__ import annotations

import unittest
from pathlib import Path

import fetch_service as fs


class TestParseNameList(unittest.TestCase):
    def test_paste_with_comma_name(self):
        out = fs.parse_name_list("Zhang, Wei\nLi, Hong\nWang, Xiaoming")
        self.assertEqual([o["authfull"] for o in out], ["Zhang, Wei", "Li, Hong", "Wang, Xiaoming"])
        self.assertEqual([o["src_id"] for o in out], [1, 2, 3])

    def test_paste_with_header(self):
        out = fs.parse_name_list("authfull\nZhang\nLi")
        self.assertEqual([o["authfull"] for o in out], ["Zhang", "Li"])

    def test_csv_with_quoted_comma(self):
        out = fs.parse_name_list('authfull,inst\n"Zhang, Wei",Peking\n"Li, Hong",Tsinghua')
        self.assertEqual([o["authfull"] for o in out], ["Zhang, Wei", "Li, Hong"])

    def test_empty(self):
        self.assertEqual(fs.parse_name_list(""), [])
        self.assertEqual(fs.parse_name_list("\n  \n"), [])

    def test_chinese_header(self):
        out = fs.parse_name_list("姓名\n张三\n李四")
        self.assertEqual([o["authfull"] for o in out], ["张三", "李四"])


class TestRunState(unittest.TestCase):
    def test_snapshot(self):
        st = fs.RunState()
        st.status = "running"
        st.total = 5
        s = st.snapshot()
        self.assertEqual(s["status"], "running")
        self.assertEqual(s["total"], 5)


class TestPresetSubset(unittest.TestCase):
    def test_preset_subset(self):
        if not fs.PRESET_XLSX.exists():
            self.skipTest("预置名单文件不存在")
        out = fs.load_preset_subset(5)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["src_id"], 1)
        self.assertTrue(all(o["authfull"] for o in out))

    def test_preset_offset(self):
        if not fs.PRESET_XLSX.exists():
            self.skipTest("预置名单文件不存在")
        out = fs.load_preset_subset(3, offset=2)
        self.assertEqual(out[0]["src_id"], 3)
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
