"""回归测试：WSL / 原生 Linux 路径与 psql 调用模式切换。

运行：
    cd pipeline && python -m unittest discover -s tests -v
只用标准库 unittest + mock，不访问 OpenAlex，也不访问数据库。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import config  # noqa: E402
import dbutil  # noqa: E402


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if k not in ("OA_PATH_MODE", "WSL_DISTRO_NAME")}


class TestPathMode(unittest.TestCase):
    def test_explicit_wsl_keeps_original_conversion(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            os.environ["OA_PATH_MODE"] = "wsl"
            self.assertEqual(config.path_mode(), "wsl")
            self.assertEqual(config.pg_path(Path("/tmp/a.sql")), "/mnt/c/tmp/a.sql")
            self.assertEqual(config.pg_path(r"C:\Users\x\a.sql"), "/mnt/c/Users/x/a.sql")
            # 已经是 /mnt/c 开头时不能重复拼接
            self.assertEqual(config.pg_path("/mnt/c/Users/x/a.sql"), "/mnt/c/Users/x/a.sql")

    def test_explicit_linux_returns_native_path(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            os.environ["OA_PATH_MODE"] = "linux"
            self.assertEqual(config.path_mode(), "linux")
            # 用字符串：Path 在 Windows 上会把 POSIX 路径转成反斜杠
            self.assertEqual(config.pg_path("/srv/openalex/x.sql"),
                             "/srv/openalex/x.sql")

    def test_invalid_mode_raises(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            os.environ["OA_PATH_MODE"] = "bogus"
            with self.assertRaises(ValueError):
                config.path_mode()

    def test_autodetect_wsl_distro(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            os.environ["WSL_DISTRO_NAME"] = "Debian"
            self.assertEqual(config.path_mode(), "wsl")

    def test_default_is_native_linux(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True), \
             mock.patch.object(os, "name", "posix"):
            self.assertEqual(config.path_mode(), "linux")

    def test_default_on_windows_is_wsl(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True), \
             mock.patch.object(os, "name", "nt"):
            self.assertEqual(config.path_mode(), "wsl")


class TestPsqlCommand(unittest.TestCase):
    def test_linux_calls_psql_directly(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            os.environ["OA_PATH_MODE"] = "linux"
            cmd = dbutil.psql_command(sql="SELECT 1")
            self.assertEqual(cmd[0], "psql")
            self.assertIn("-v", cmd)
            self.assertIn("ON_ERROR_STOP=1", cmd)

    def test_wsl_wraps_with_wsl_bash(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            os.environ["OA_PATH_MODE"] = "wsl"
            cmd = dbutil.psql_command(sql="SELECT 1")
            self.assertEqual(cmd[:3], ["wsl", "-e", "bash"])
            self.assertIn("psql", cmd[-1])


if __name__ == "__main__":
    unittest.main()
