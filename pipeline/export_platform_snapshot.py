#!/usr/bin/env python3
"""Export a read-only snapshot of the OpenAlex analysis layer for the phase-2 platform.

Run under WSL (psycopg2 venv):
  export PGPASSWORD=$(cat ~/.openalex_db_pass)
  $HOME/.venvs/openalex/bin/python pipeline/export_platform_snapshot.py

Writes:
  web/data/snapshot.json  (machine-readable)
  web/data/snapshot.js    (file:// friendly, window.OA_SNAPSHOT)

No API calls, no writes to the database. The snapshot is deliberately aggregated:
it contains no personal contact data and no API keys.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "web" / "data"

RULE_EVENT = "A_1000_v1"
RULE_COHORT = "CN_US_2017_v1"
RULE_DID = "DID_2018_A1000_v2"


def fetchall(cur, sql: str, params=None):
    cur.execute(sql, params or ())
    return [dict(r) for r in cur.fetchall()]


def build_snapshot(conn) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        summary = fetchall(cur, """
            SELECT
                (SELECT count(*) FROM scholar) AS scholars,
                (SELECT count(*) FROM work) AS works,
                (SELECT count(*) FROM authorship) AS authorships,
                (SELECT count(*) FROM institution) AS institutions,
                (SELECT count(*) FROM annual_panel) AS annual_rows,
                (SELECT count(*) FROM mobility_event WHERE rule_version=%s) AS events,
                (SELECT count(*) FROM event_evidence) AS evidence_rows,
                (SELECT count(*) FROM cohort_member) AS cohort_members,
                (SELECT count(*) FROM cohort_external_review WHERE eligible_treated) AS treated
        """, (RULE_EVENT,))[0]

        classification = fetchall(cur, """
            SELECT observed_class,
                   count(*) AS observed,
                   count(*) FILTER (WHERE is_classified) AS formal,
                   count(*) FILTER (WHERE NOT is_classified) AS uncertain
            FROM mobility_class
            WHERE rule_version=%s
            GROUP BY observed_class
            ORDER BY observed_class
        """, (RULE_EVENT,))

        events_by_type = fetchall(cur, """
            SELECT event_type,
                   count(*) AS total,
                   count(*) FILTER (WHERE evidence_level='Confirmed') AS confirmed,
                   count(*) FILTER (WHERE evidence_level='Probable') AS probable,
                   count(*) FILTER (WHERE evidence_level='Possible') AS possible
            FROM mobility_event
            WHERE rule_version=%s
            GROUP BY event_type
            ORDER BY event_type
        """, (RULE_EVENT,))

        events_by_year = fetchall(cur, """
            SELECT paper_year AS year, count(*) AS total,
                   count(*) FILTER (WHERE from_country IS DISTINCT FROM to_country) AS cross_border
            FROM mobility_event
            WHERE rule_version=%s AND paper_year BETWEEN 1990 AND 2026
            GROUP BY paper_year
            ORDER BY paper_year
        """, (RULE_EVENT,))

        country_flows = fetchall(cur, """
            SELECT from_country, to_country, count(*) AS total
            FROM mobility_event
            WHERE rule_version=%s
              AND from_country IS NOT NULL AND to_country IS NOT NULL
              AND is_suppressed IS NOT TRUE
            GROUP BY from_country, to_country
            ORDER BY total DESC
            LIMIT 40
        """, (RULE_EVENT,))
        for row in country_flows:
            row["cross_border"] = row["from_country"] != row["to_country"]

        top_institutions = fetchall(cur, """
            SELECT i.institution_id, i.canonical_name AS name, i.country_code AS country,
                   i.city, i.lat, i.lon, count(*) AS total
            FROM mobility_event e
            JOIN institution i ON i.institution_id = e.to_inst
            WHERE e.rule_version=%s
            GROUP BY i.institution_id, i.canonical_name, i.country_code, i.city, i.lat, i.lon
            ORDER BY total DESC
            LIMIT 60
        """, (RULE_EVENT,))

        cohort = fetchall(cur, """
            SELECT s.scholar_id, s.display_name AS name,
                   cm.birth_country, cm.current_country, cm.confidence,
                   cm.determination_method,
                   rev.verdict, rev.eligible_treated, rev.source_count
            FROM cohort_member cm
            JOIN scholar s ON s.scholar_id = cm.scholar_id
            LEFT JOIN cohort_external_review rev
              ON rev.scholar_id = cm.scholar_id AND rev.rule_version=%s
            WHERE cm.confidence='high' OR rev.eligible_treated IS TRUE
            ORDER BY (rev.eligible_treated IS TRUE) DESC, cm.confidence, s.display_name
            LIMIT 60
        """, (RULE_DID,))

        did_estimates = fetchall(cur, """
            SELECT specification, outcome, coefficient, std_error, t_stat, p_value,
                   ci_low, ci_high, n_obs, n_scholars, n_clusters, n_treated,
                   n_control, joint_f_stat, joint_p_value, parallel_trend_passed, note
            FROM did_robust_estimate
            WHERE rule_version=%s
            ORDER BY outcome, specification
        """, (RULE_DID,))

        did_annual = fetchall(cur, """
            SELECT outcome, year, lead_or_lag, coefficient, std_error, p_value, ci_low, ci_high
            FROM did_robust_annual_effect
            WHERE rule_version=%s
            ORDER BY outcome, year
        """, (RULE_DID,))

        # Trajectories for the scholars with the richest event evidence, capped for size.
        rich_ids = [r["scholar_id"] for r in fetchall(cur, """
            SELECT scholar_id FROM mobility_event
            WHERE rule_version=%s AND is_suppressed IS NOT TRUE
            GROUP BY scholar_id ORDER BY count(*) DESC LIMIT 12
        """, (RULE_EVENT,))]

        trajectories = []
        for sid in rich_ids:
            meta = fetchall(cur, """
                SELECT s.scholar_id, s.display_name AS name,
                       mc.observed_class, mc.formal_class, mc.is_classified
                FROM scholar s
                LEFT JOIN mobility_class mc
                  ON mc.scholar_id=s.scholar_id AND mc.rule_version=%s
                WHERE s.scholar_id=%s
            """, (RULE_EVENT, sid))
            years = fetchall(cur, """
                SELECT year, primary_country AS country, primary_institution_id AS inst_id,
                       primary_is_tie AS tie, valid_works AS works, observation_status
                FROM annual_panel WHERE scholar_id=%s ORDER BY year
            """, (sid,))
            events = fetchall(cur, """
                SELECT event_type, paper_year, from_country, to_country,
                       from_inst, to_inst, evidence_level, status, is_suppressed
                FROM mobility_event
                WHERE scholar_id=%s AND rule_version=%s
                ORDER BY paper_year, event_type
            """, (sid, RULE_EVENT))
            trajectories.append({**(meta[0] if meta else {"scholar_id": sid, "name": "?"}),
                                 "years": years, "events": events})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": {
            "event_rule": RULE_EVENT,
            "cohort_rule": RULE_COHORT,
            "did_rule": RULE_DID,
            "sample": "1,000 人试跑（对外须标注为候选层，不等同全量交付）",
        },
        "summary": summary,
        "classification": classification,
        "events_by_type": events_by_type,
        "events_by_year": events_by_year,
        "country_flows": country_flows,
        "top_institutions": top_institutions,
        "cohort": cohort,
        "did_estimates": did_estimates,
        "did_annual": did_annual,
        "trajectories": trajectories,
    }


def main():
    dsn = {
        "host": os.environ.get("PGHOST", "127.0.0.1"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "user": os.environ.get("PGUSER", "postgres"),
        "password": os.environ.get("PGPASSWORD", ""),
        "dbname": os.environ.get("PGDATABASE", "openalex"),
    }
    if not dsn["password"]:
        raise SystemExit("PGPASSWORD is required; export it from ~/.openalex_db_pass first.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(**dsn)
    try:
        snapshot = build_snapshot(conn)
    finally:
        conn.close()

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
    (OUT_DIR / "snapshot.json").write_text(payload + "\n", encoding="utf-8")
    (OUT_DIR / "snapshot.js").write_text(
        "window.OA_SNAPSHOT = " + payload + ";\n", encoding="utf-8"
    )
    print(f"snapshot written: {OUT_DIR / 'snapshot.json'} ({len(payload):,} chars)")


if __name__ == "__main__":
    main()
