#!/usr/bin/env python3
"""koi_drift_sweep.py — read-only federation row-count parity check.

Runs weekly on the NUC (dobby-drift-sweep.timer, Sunday 06:00 PDT). Compares
row counts for the 4 federated tables between the NUC's local Postgres and the
MacBook's, reached via SSH over WireGuard — the same NUC->MacBook pattern used
by dobby/scripts/mirror-ac6-check.sh. (The federation event path is HTTP/koi-net,
not direct Postgres, so the MacBook's Postgres is not exposed on the WG network;
SSH is the connection mechanism.)

READ-ONLY. SELECT COUNT(*) only. Never writes to either database.

Every run appends exactly one status line to the drift log. Drift is measured
as movement of the NUC-vs-MacBook row-count GAP away from the 2026-05-13
reconciliation baseline — NOT the raw gap itself. Reconciliation was a
one-directional MacBook->NUC backfill, so NUC is an intentional superset; the
baseline gap is by design (see BASELINE_GAPS). When any table's gap has moved
from its baseline by more than 5% OF TABLE SIZE, the line is tagged status=DRIFT and
dobby/scripts/briefing.sh surfaces a "Federation parity" warning in the next
morning brief, using the same DATA_WARNINGS mechanism as the PL-5 drift
sentinel. This is the safety net the federation plan's risk-acceptance for the
post-commit emit race depends on.

Note: this is NOT the drift *sentinel* (cross-type entity duplicates within a
single node, inline in briefing.sh). This is the drift *sweep* — row-count
divergence *between* the two nodes. Different question, separate code.

Graceful degradation: if the MacBook is unreachable (WireGuard down, SSH fails),
logs status=WG_UNREACHABLE and exits 0. An unreachable peer is not drift.

Drift log: /var/log/koi/drift.log if writable, else ~/.config/dobby/drift.log.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import os

# The 4 federated tables (plan: koi-graph-graceful-toucan.md, step 17).
TABLES = (
    "entity_registry",
    "knowledge_facts",
    "knowledge_episodes",
    "document_entity_links",
)

DRIFT_THRESHOLD_PCT = 5.0

# Per-table baseline gap (NUC count - MacBook count) as of the 2026-05-13
# reconciliation (see ~/.claude/plans/koi-graph-reconciliation.report.md, Phase H
# count table). Reconciliation was a one-directional MacBook->NUC backfill, so
# NUC is an intentional superset — this gap is NOT drift, it is the designed
# baseline. Drift = how far the *current* gap has moved from these values.
#
# RE-SNAPSHOT these after any future reconciliation, or once Phase 5 federation
# cutover has stabilized the two nodes (federation will converge the gap toward
# a new steady state). Stale baselines produce false DRIFT alerts.
BASELINE_GAPS = {
    "entity_registry": 247,        # NUC 10,439 - MacBook 10,192
    "knowledge_facts": 3197,       # NUC 17,981 - MacBook 14,784
    "knowledge_episodes": 159,     # NUC  4,159 - MacBook  4,000
    "document_entity_links": 1602, # NUC 51,245 - MacBook 49,643
}

# Local NUC Postgres — same connection string briefing.sh uses (local socket,
# default user). Works identically on the MacBook side (user = SSH user).
PG_URL = os.environ.get("DRIFT_SWEEP_PG_URL", "postgresql:///personal_koi")

# MacBook SSH target — same env-var names + defaults as mirror-ac6-check.sh.
MB_USER = os.environ.get("MACBOOK_SSH_USER", "darrenzal")
MB_HOST = os.environ.get("MACBOOK_SSH_HOST", "10.100.0.2")
# psql is not on the non-interactive PATH on macOS; use the absolute path.
MB_PSQL = os.environ.get("MACBOOK_PSQL_BIN", "/opt/homebrew/bin/psql")

# One round-trip: a single row, pipe-separated counts in TABLES order.
COUNT_SQL = "SELECT " + ", ".join(f"(SELECT COUNT(*) FROM {t})" for t in TABLES) + ";"


def _pick_drift_log() -> Path | None:
    """First writable of /var/log/koi/drift.log, ~/.config/dobby/drift.log."""
    for path in (Path("/var/log/koi/drift.log"),
                 Path.home() / ".config" / "dobby" / "drift.log"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a"):
                pass
            return path
        except OSError:
            continue
    return None


def _parse_counts(raw: str) -> list[int]:
    fields = raw.strip().split("|")
    if len(fields) != len(TABLES):
        raise ValueError(f"expected {len(TABLES)} counts, got {raw!r}")
    return [int(f.strip()) for f in fields]


def _count_local() -> list[int]:
    out = subprocess.run(
        ["psql", PG_URL, "-tAc", COUNT_SQL],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return _parse_counts(out.stdout)


def _count_macbook() -> list[int]:
    # SSH to the MacBook and run psql there against its local socket.
    remote_cmd = f'{MB_PSQL} {PG_URL} -tAc "{COUNT_SQL}"'
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
         "-o", "StrictHostKeyChecking=no", f"{MB_USER}@{MB_HOST}", remote_cmd],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return _parse_counts(out.stdout)


def _emit(line: str, dry_run: bool) -> None:
    if dry_run:
        log_path = _pick_drift_log()
        dest = str(log_path) if log_path else "(no writable drift log)"
        print(f"[dry-run] would append to {dest}:\n  {line}")
        return
    log_path = _pick_drift_log()
    if log_path is None:
        # Nowhere to persist — at least surface it on stdout for journald.
        print(line)
        print("[drift-sweep] WARNING: no writable drift log location", file=sys.stderr)
        return
    with log_path.open("a") as fh:
        fh.write(line + "\n")
    print(f"[drift-sweep] logged to {log_path}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Federation row-count drift sweep.")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the counts but do not write the drift log")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Local NUC counts — a failure here is a genuine fault (NUC's own DB).
    try:
        nuc_counts = _count_local()
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        _emit(f'{ts} status=ERROR detail="local count failed: {exc}"', args.dry_run)
        print(f"[drift-sweep] local count failed: {exc}", file=sys.stderr)
        return 1

    # MacBook counts — unreachable is graceful degradation, NOT an error.
    try:
        mb_counts = _count_macbook()
    except subprocess.TimeoutExpired:
        _emit(f'{ts} status=WG_UNREACHABLE detail="ssh {MB_USER}@{MB_HOST} timed out"',
              args.dry_run)
        print("[drift-sweep] MacBook unreachable (timeout) — sweep skipped",
              file=sys.stderr)
        return 0
    except subprocess.CalledProcessError as exc:
        err_lines = (exc.stderr or "").strip().splitlines()
        detail = err_lines[-1] if err_lines else f"ssh exited {exc.returncode}"
        _emit(f'{ts} status=WG_UNREACHABLE detail="{detail}"', args.dry_run)
        print(f"[drift-sweep] MacBook unreachable — sweep skipped: {detail}",
              file=sys.stderr)
        return 0
    except (ValueError, OSError) as exc:
        _emit(f'{ts} status=WG_UNREACHABLE detail="{exc}"', args.dry_run)
        print(f"[drift-sweep] MacBook count unusable — sweep skipped: {exc}",
              file=sys.stderr)
        return 0

    # Both sides counted — compute per-table drift vs the reconciliation baseline.
    # The raw NUC-MacBook gap is intentionally non-zero (reconciliation made NUC a
    # superset); drift is how far the *current* gap has moved from BASELINE_GAPS.
    #
    # Denominator is TABLE SIZE, not the baseline gap. Using the baseline gap as
    # the denominator makes a table's alert sensitivity depend on how large its
    # historical asymmetry happened to be — arbitrary (entity_registry's 247-row
    # baseline would be ~13x more alert-sensitive than knowledge_facts' 3197).
    # Table size answers the question that matters: "what fraction of the table
    # has anomalously diverged." A systematic federation failure shows as a
    # growing fraction-of-table; normal pre-cutover extraction divergence (tens
    # of rows/day on 10k-row tables) stays well under threshold.
    parts: list[str] = []
    max_drift = 0.0
    for table, nuc, mb in zip(TABLES, nuc_counts, mb_counts):
        current_gap = nuc - mb
        baseline_gap = BASELINE_GAPS.get(table, 0)
        gap_delta = current_gap - baseline_gap
        drift = abs(gap_delta) / max(mb, 1) * 100
        max_drift = max(max_drift, drift)
        parts.append(
            f"{table}[nuc={nuc} mb={mb} gap={current_gap} "
            f"baseline_gap={baseline_gap} gap_delta={gap_delta:+d} drift={drift:.2f}%]"
        )

    status = "DRIFT" if max_drift > DRIFT_THRESHOLD_PCT else "OK"
    line = f"{ts} status={status} max_drift_pct={max_drift:.2f} " + " ".join(parts)
    _emit(line, args.dry_run)
    print(f"[drift-sweep] {status} (max drift {max_drift:.2f}%)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
