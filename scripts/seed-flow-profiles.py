#!/usr/bin/env python3
"""
Seed flow_profiles from existing history (#183 build #1, part c).

Establishes a starting rolling-average baseline so the pre-flight cost
estimator has numbers before any new runs accrue. Sources:

  - llm_tokens  -> AVG(tokens_total) per operation_label in jw_metrics
                   (operation_label "flow/step" -> (flow, step))
  - asr_minutes -> avg source-video minutes from social_queue.duration_seconds
                   (rough proxy; self-corrects once the ASR path logs live)

Idempotent and non-destructive: uses ON CONFLICT DO NOTHING, so a rerun never
clobbers profiles that live runs have since updated.

Usage (inside a Cove container, DATABASE_URL is set):
    python scripts/seed-flow-profiles.py
Or point it explicitly:
    DATABASE_URL=postgresql://user:pw@host:5432/db python scripts/seed-flow-profiles.py
"""

import os
import sys

import psycopg

SEED_SQL = """
    INSERT INTO flow_profiles (flow, step, unit_kind, avg_units, sample_count, last_units)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (flow, step, unit_kind) DO NOTHING
"""


def _parse_label(label: str) -> tuple[str, str]:
    label = (label or "").strip() or "unknown"
    if "/" in label:
        flow, step = label.split("/", 1)
        return flow.strip() or "unknown", step.strip() or "*"
    return label, "*"


def seed_llm_tokens(conn) -> int:
    cur = conn.execute(
        """SELECT operation_label, AVG(tokens_total)::float AS avg_units,
                  COUNT(*) AS n
           FROM jw_metrics
           WHERE tokens_total IS NOT NULL AND tokens_total > 0
             AND COALESCE(succeeded, TRUE)
           GROUP BY operation_label"""
    )
    rows = cur.fetchall()
    written = 0
    for label, avg_units, n in rows:
        flow, step = _parse_label(label)
        conn.execute(SEED_SQL, (flow, step, "llm_tokens",
                                float(avg_units), int(n), float(avg_units)))
        written += 1
    return written


def seed_asr_minutes(conn) -> int:
    # social_queue.duration_seconds may be absent on some Coves; degrade cleanly.
    try:
        cur = conn.execute(
            """SELECT AVG(src_minutes)::float AS avg_units, COUNT(*) AS n FROM (
                   SELECT COALESCE(source_stem, file_path) AS src,
                          SUM(duration_seconds) / 60.0 AS src_minutes
                   FROM social_queue
                   WHERE duration_seconds IS NOT NULL AND duration_seconds > 0
                   GROUP BY COALESCE(source_stem, file_path)
               ) s"""
        )
        row = cur.fetchone()
    except Exception as e:
        conn.rollback()
        print(f"  asr_minutes: skipped (no usable social_queue data: {e})")
        return 0
    if not row or row[0] is None or not row[1]:
        print("  asr_minutes: no video duration data to seed")
        return 0
    avg_units, n = float(row[0]), int(row[1])
    conn.execute(SEED_SQL, ("video-pipeline", "transcribe", "asr_minutes",
                            avg_units, n, avg_units))
    return 1


def main() -> int:
    db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if not db_url:
        print("ERROR: set DATABASE_URL (or DB_URL) to the Cove database.")
        return 2
    with psycopg.connect(db_url, autocommit=False) as conn:
        llm_n = seed_llm_tokens(conn)
        asr_n = seed_asr_minutes(conn)
        conn.commit()
    print(f"Seeded flow_profiles: {llm_n} llm_tokens rows, {asr_n} asr_minutes rows.")
    print("(ON CONFLICT DO NOTHING — existing/live-updated profiles untouched.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
