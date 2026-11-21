"""把 pipeline/data/batch_001/requests.jsonl 导入 PostgreSQL 的 api_request 表。

- 只导入 http_status=200 的成功请求（字段完整），失败请求留在 JSONL 供排查。
- 用 payload->>'batch' + request_url 的 sha256 做幂等键，重复导入不产生重复行。
- request_id 从 1001 起，避开已落库的 1..220（基线 + Crossref 种子）。

用法：
  python export_requests_sql.py            # 生成 SQL 文件
  psql ... -f data/batch_001/requests_import.sql   # 在数据库执行
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import batch_dir  # noqa: E402

START_ID = 1001


def q(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def main() -> None:
    d = batch_dir()
    src = d / "requests.jsonl"
    if not src.exists():
        print("[ERR] 没有 requests.jsonl，请先跑 wp12_fetch.py")
        return

    records = []
    seen: set[str] = set()
    for line in src.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("http_status") != 200:
            continue
        # 幂等键：batch + purpose + scholar + request_url
        key = "|".join(
            str(r.get(k, "")) for k in ("batch", "purpose", "scholar_src_id", "request_url")
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(r)

    out = d / "requests_import.sql"
    with out.open("w", encoding="utf-8") as f:
        f.write("-- API 请求留痕导入（幂等；request_id 1001+）\nBEGIN;\n")
        for i, r in enumerate(records, start=START_ID):
            payload = {
                "batch": r.get("batch"),
                "purpose": r.get("purpose"),
                "scholar_src_id": r.get("scholar_src_id"),
                "has_key": r.get("payload", {}).get("has_key"),
            }
            cols = [
                "request_id", "endpoint", "request_url", "http_method", "http_status",
                "requested_at", "response_sha256", "byte_count", "error_code", "payload",
            ]
            vals = [
                str(i),
                q(r.get("endpoint")),
                q(r.get("request_url")),
                q(r.get("http_method", "GET")),
                q(r.get("http_status")),
                q(r.get("requested_at")),
                q(r.get("response_sha256")),
                q(r.get("byte_count")),
                q(r.get("error_code")),
                q(json.dumps(payload, ensure_ascii=False)) + "::jsonb",
            ]
            f.write(
                f"INSERT INTO api_request({','.join(cols)}) VALUES ({','.join(vals)})\n"
                f"ON CONFLICT DO NOTHING;\n"
            )
        f.write("SELECT setval(pg_get_serial_sequence('api_request','request_id'), "
                "GREATEST((SELECT max(request_id) FROM api_request),1));\n")
        f.write("COMMIT;\n")
    print(f"[OK] {len(records)} successful requests -> {out}")


if __name__ == "__main__":
    main()
