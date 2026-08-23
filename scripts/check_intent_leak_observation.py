#!/usr/bin/env python3
"""Evaluate koi task 7878 without allowing its 24-hour gate to close early."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


DEFAULT_CUTOVER = "2026-08-22T13:47:00-07:00"
TASK_ID = 7878
TASK_KEY = "koi-intent-orphan-leak-ac1-24h-check"
POSITIVE_CONTROL_TABLE = "entity_registry_backup_intent_fixtures_20260822"


class ObservationError(RuntimeError):
    """The observation could not be evaluated safely."""


def parse_cutover(value: str) -> datetime:
    try:
        cutover = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ObservationError(f"Invalid ISO cutover: {value}") from exc
    if cutover.tzinfo is None:
        raise ObservationError("Cutover must include an explicit UTC offset")
    return cutover


def classify_observation(
    *,
    database_now: datetime,
    deadline: datetime,
    nonconforming: int,
    orphaned: int,
) -> tuple[str, int]:
    if database_now < deadline:
        return "incomplete", 2
    if nonconforming == 0 and orphaned == 0:
        return "pass", 0
    return "fail", 3


def evaluate(
    conn,
    *,
    cutover: datetime,
    minimum_hours: float,
    task_id: int = TASK_ID,
) -> tuple[dict[str, Any], int]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT now() AS database_now")
        database_now = cur.fetchone()["database_now"]
        deadline = cutover + timedelta(hours=minimum_hours)

        cur.execute(
            """
            SELECT
              count(*) AS total,
              count(*) FILTER (
                WHERE resolution_tier IS NULL
                   OR source IS DISTINCT FROM 'intent-registry'
                   OR metadata->>'intent_key' IS NULL
              ) AS nonconforming,
              count(*) FILTER (
                WHERE NOT EXISTS (
                  SELECT 1 FROM intent_registry ir WHERE ir.entity_uri=er.fuseki_uri
                )
              ) AS orphaned
            FROM entity_registry er
            WHERE entity_type='Intent'
              AND created_at > (%s::timestamptz AT TIME ZONE current_setting('TimeZone'))
            """,
            (cutover,),
        )
        observed = dict(cur.fetchone())

        cur.execute("SELECT to_regclass(%s) AS backup", (f"public.{POSITIVE_CONTROL_TABLE}",))
        backup_exists = cur.fetchone()["backup"] is not None
        positive_control = None
        if backup_exists:
            # The fixtures were safely purged from the live table, so the
            # retained snapshot is the only honest pre-cutover control.
            cur.execute(
                f"""
                SELECT count(*) AS total FROM {POSITIVE_CONTROL_TABLE}
                WHERE entity_type='Intent'
                  AND created_at > (
                    (%s::timestamptz - interval '24 hours')
                    AT TIME ZONE current_setting('TimeZone')
                  )
                  AND created_at <= (
                    %s::timestamptz AT TIME ZONE current_setting('TimeZone')
                  )
                """,
                (cutover, cutover),
            )
            positive_control = int(cur.fetchone()["total"])

        cur.execute(
            "SELECT id, task_key, status, completed_at FROM task_registry WHERE id=%s",
            (task_id,),
        )
        task = cur.fetchone()
    if task is None or task["task_key"] != TASK_KEY:
        raise ObservationError(f"Task {task_id} is absent or has the wrong task_key")

    elapsed = database_now >= deadline
    verdict, exit_code = classify_observation(
        database_now=database_now,
        deadline=deadline,
        nonconforming=int(observed["nonconforming"]),
        orphaned=int(observed["orphaned"]),
    )
    report = {
        "verdict": verdict,
        "task": dict(task),
        "cutover": cutover.isoformat(),
        "deadline": deadline.isoformat(),
        "database_now": database_now.isoformat(),
        "minimum_hours": minimum_hours,
        "window_elapsed": elapsed,
        "observed": {key: int(value) for key, value in observed.items()},
        "positive_control": {
            "table": POSITIVE_CONTROL_TABLE,
            "available": backup_exists,
            "pre_cutover_24h_rows": positive_control,
        },
    }
    return report, exit_code


def close_task(conn, task_id: int = TASK_ID) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE task_registry
            SET status='done', completed_at=NOW(), updated_at=NOW()
            WHERE id=%s AND task_key=%s AND status <> 'done'
            """,
            (task_id, TASK_KEY),
        )
        if cur.rowcount != 1:
            raise ObservationError(
                f"Task {task_id} was not open or changed before completion"
            )
    conn.commit()


def connect(database: str):
    return psycopg2.connect(database) if "://" in database else psycopg2.connect(dbname=database)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="personal_koi")
    parser.add_argument("--cutover", default=DEFAULT_CUTOVER)
    parser.add_argument("--minimum-hours", type=float, default=24.0)
    parser.add_argument("--task-id", type=int, default=TASK_ID)
    parser.add_argument(
        "--close",
        action="store_true",
        help="Mark the exact task done, but only after a passing elapsed window",
    )
    args = parser.parse_args()
    cutover = parse_cutover(args.cutover)
    conn = connect(args.database)
    try:
        report, exit_code = evaluate(
            conn,
            cutover=cutover,
            minimum_hours=args.minimum_hours,
            task_id=args.task_id,
        )
        if args.close:
            if exit_code != 0:
                raise ObservationError(
                    f"Refusing to close task: observation verdict is {report['verdict']}"
                )
            close_task(conn, args.task_id)
            report["task_closed"] = True
        else:
            report["task_closed"] = False
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return exit_code
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ObservationError as exc:
        raise SystemExit(f"ERROR: {exc}")
