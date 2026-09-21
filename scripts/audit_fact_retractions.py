#!/usr/bin/env python3
"""Issue #67 — read-only historical audit of retracted facts vs. federation history.

Answers, for every `knowledge_facts` row with `valid_to IS NOT NULL` (the
locally retracted set), the question the incident could not: *which peers may
still hold this fact live, and what would a repair send?* It reads three
surfaces and writes nothing:

  koi_net_events   every event that CARRIED the fact — a `knowledge_episode`
                   whose payload.facts[] contains the id, or a `knowledge_fact`
                   event whose payload.id is the id. The carried copy is a
                   LIVE COPY if its valid_to is null, a TOMBSTONE otherwise.
  koi_net_edges    this node's outbound edges, any status. Scope is decided by
                   `api.fact_retraction.scope_admits` — the mirror of the poll
                   filter — never re-implemented here.
  the 127 ledger   `knowledge_fact_retractions` / `..._deliveries` when
                   migration 127 is applied; reported as absent otherwise.

EVIDENCE RULES (proved in tests/test_fact_retraction_boundary.py)
  * `delivered_to` is NOT evidence of transmission. poll() appends it for
    events the edge scope EXCLUDED as well as for events it handed over. A
    peer whose only mark is delivered_to is `unverifiable`, never
    `possibly_live`.
  * `confirmed_by` is receipt, not application. A confirmed exact tombstone
    is `tombstone_confirmed` with application_proven=False. Only the ledger's
    `applied` state — the recipient's own report — proves application.
  * A tombstone "matches" only if the carried valid_to equals the committed
    knowledge_facts.valid_to as an instant, microseconds included. Anything
    else is `tombstone_valid_to_mismatch`.
  * The plan carries `valid_to` as `.isoformat()` of the DB value. Never the
    clock, never a reconstruction.

CLASSIFICATION per (fact, peer), first match wins:
  history_unknown             zero carrying rows AND the fact predates the
                              oldest koi_net_events row (cleanup may have
                              deleted its history)
  applied / rejected / failed the 127 ledger's terminal verdicts, verbatim
  pending                     the 127 ledger says the peer reported the fact
                              ABSENT there and recorded a pending tombstone —
                              nothing live to repair, application NOT proven
  tombstone_confirmed         exact tombstone in confirmed_by (or ledger
                              `received`) — receipt; application unproven
  tombstone_valid_to_mismatch the only confirmed tombstone carries a
                              different valid_to
  tombstone_unconfirmed       an exact tombstone was addressed to the peer
                              (unicast target, still-pollable broadcast,
                              ledger queued/delivered/retrying — or a
                              delivered_to mark on a peer that CONFIRMED the
                              live copy) with no receipt
  possibly_live               live copy CONFIRMED, no tombstone evidence
  unverifiable                live copy delivered_to-only, no tombstone
                              receipt; or ledger `unverifiable`. A
                              delivered_to mark on the tombstone changes
                              nothing here — two non-evidence marks are not
                              evidence (the incident's three excluded peers)
  never_sent                  no evidence this peer was ever handed the fact
  unauthorized                any of possibly_live / unverifiable /
                              tombstone_unconfirmed / tombstone_valid_to_
                              mismatch where the peer's edge does not admit
                              knowledge_fact NOW — cannot be repaired under
                              policy; the underlying class is kept in
                              `evidence_class`

DRY-RUN PLAN: one line per (fact, peer) in possibly_live / unverifiable /
tombstone_unconfirmed / tombstone_valid_to_mismatch whose scope admits
knowledge_fact now. An in-flight exact tombstone (unexpired, still pollable)
is `wait`, not a duplicate queue. `unauthorized` rows are listed under
`blocked` for the operator. Nothing is written: `--apply` is refused (exit 2)
— repair application is a #67 follow-up, not implemented in this branch.

READ-ONLY BY MECHANISM: main() runs `SET default_transaction_read_only = on`
on its connection before opening the transaction the audit runs in, so an
accidental write raises `ReadOnlySQLTransactionError` instead of landing.
A run against the live `personal_koi` is therefore safe and expected.

EXIT CODES
  0  no outstanding obligations
  1  outstanding obligations (plan non-empty, or possibly_live / unverifiable
     / unauthorized / tombstone_valid_to_mismatch / history_unknown /
     peer_unmigrated / scope_failed_reopenable present, or a TERMINAL failure
     present. Terminal = any ledger `failed` that is not currently reopenable
     (attempts exhausted, or a scope failure while the edge is still narrow)
     and any non-reopenable ledger `rejected` — INCLUDING
     `peer_holds_different_valid_to`, which keeps its classification
     tombstone_valid_to_mismatch but is listed under "terminal failures" with
     its ledger state, so it cannot vanish between plan (futile), blocked
     (not unauthorized) and terminal (third review, finding N4))
  2  --apply refused
  3  misconfigured (cannot connect, node RID undeterminable, read-only
     mode not in effect, `--scope-enforced-since auto` unverifiable)

  The exit code and `outstanding` are SELECTION-scoped: with --limit,
  --fact-id or --since they speak for the selected facts only. The
  population counts in the header are always whole-database.

USAGE
  scripts/audit_fact_retractions.py [--dsn DSN] [--node-rid RID]
      [--fact-id UUID ...] [--since ISO] [--limit N] [--json]
      [--scope-enforced-since ISO|auto]

SCOPE ENFORCEMENT FLOOR (`scope_excluded`)
  A delivered_to mark is a PROVABLE poll-filter exclusion only if the event
  was queued after this node's SERVICE began enforcing per-edge scoping
  (commit 2c497f0, 2026-08-25 19:50:25 -0700, first served by whichever
  process restart loaded it). That instant is a property of the serving
  process of the audited node — not of this file, not of the checkout this
  file happens to sit in (third review, finding N2: at c41fa77 `auto` verified
  the SCRIPT's checkout, so the worktree's copy vouched for any database).
    --scope-enforced-since <ISO>   PREFERRED. The operator asserts the instant
                                   (the first enforcing start of the audited
                                   node's service; read `ps -o lstart=` of the
                                   process that served the marks, or the
                                   deploy log). Naive values are read as UTC.
    --scope-enforced-since auto    only for a LOCAL database, and only when the
                                   serving process can actually be verified:
                                   the DSN host must be local; the process
                                   listening on --koi-port (default $KOI_PORT
                                   or 8351) is located, its cwd read from the
                                   process (/proc/<pid>/cwd, else lsof), that
                                   checkout must contain 2c497f0, and the
                                   process must have started after the commit.
                                   The floor is the commit time — a LOWER bound
                                   on enforcement: events queued between the
                                   commit and the first enforcing restart are
                                   classified as exclusions. Anything not
                                   verifiable → exit 3 with "give the instant".
    (absent)                       no floor; the class is disabled and such
                                   marks stay `unverifiable` (→ unauthorized)
  The floor used, where it came from (pid, start time, cwd), is printed in
  the report header. `auto` never consults this file's own checkout.

COST: one pass over every knowledge-carrying koi_net_events row (their
`contents` hold the fact ids and, for episodes, the embeddings — ~1.4 GB on
the laptop as of 2026-09-15, about a minute). The pass is made once and
serves both the population counts and the selection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import UUID

import asyncpg

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import fact_retraction  # noqa: E402
from api.fact_retraction import scope_admits  # noqa: E402

DEFAULT_DSN = os.getenv("POSTGRES_URL") or "postgresql://darrenzal:@localhost:5432/personal_koi"

# Quoted in docs/federation/fact-retractions.md and pinned by the tests.
READ_ONLY_SQL = "SET default_transaction_read_only = on"

EPISODE_PREFIX = fact_retraction.EPISODE_RID_PREFIX
FACT_PREFIX = fact_retraction.FACT_RID_PREFIX

# Classification vocabulary.
C_HISTORY_UNKNOWN = "history_unknown"
C_APPLIED = "applied"
C_PENDING = "pending"
C_REJECTED = "rejected"
C_FAILED = "failed"
C_TOMBSTONE_CONFIRMED = "tombstone_confirmed"
C_TOMBSTONE_MISMATCH = "tombstone_valid_to_mismatch"
C_TOMBSTONE_UNCONFIRMED = "tombstone_unconfirmed"
C_POSSIBLY_LIVE = "possibly_live"
C_UNVERIFIABLE = "unverifiable"
C_NEVER_SENT = "never_sent"
C_UNAUTHORIZED = "unauthorized"
# Added after the 2026-09-16 adversarial review:
#   scope_excluded — the only marks this peer carries are PROVABLE poll-filter
#     exclusions: the event was queued after per-edge scoping was enforced
#     (commit 2c497f0) AND after the peer's edges last changed, and none of the
#     peer's APPROVED scopes admits the event's domain. By the branch's own pin
#     (test_pin_delivered_to_marks_scope_excluded_events) that mark means the
#     peer was NOT handed the event. Not outstanding; not `unauthorized`.
#   peer_unmigrated — the ledger says the peer rejected with a
#     `ledger_unavailable…` reason: it has not applied migration 127. Sendable
#     (the sweep re-queues it); apply 127 there first.
#   scope_failed_reopenable — the ledger says `failed` for a scope reason and the
#     edge admits again now. Sendable (the sweep re-queues it).
C_SCOPE_EXCLUDED = "scope_excluded"
C_PEER_UNMIGRATED = "peer_unmigrated"
C_SCOPE_FAILED_REOPENABLE = "scope_failed_reopenable"

# Per-edge scoping of domain events was introduced by commit 2c497f0
# (2026-08-25 19:50:25 -0700). Before it every APPROVED edge received every
# domain event regardless of scope, so a delivered_to mark from before that
# instant can be a real hand-over. The instant is a property of the SERVING
# PROCESS of the audited node (when did ITS service start enforcing), so it is
# never assumed here: `resolve_scope_floor` takes it from the operator, or —
# `auto`, local databases only — verifies it against the process actually
# listening on the KOI port (third review, finding N2: the second-round `auto`
# verified the checkout this FILE sits in, which vouches for nothing). With no
# floor the `scope_excluded` class is disabled. SCOPE_ENFORCEMENT_COMMIT_TIME
# is the committer time, kept for tests and messages; `auto` reads it from git.
SCOPE_ENFORCEMENT_COMMIT = "2c497f0"
SCOPE_ENFORCEMENT_COMMIT_TIME = datetime(2026, 8, 26, 2, 50, 25, tzinfo=timezone.utc)
def _default_koi_port() -> int:
    """The service's own variable is KOI_API_PORT (~/.config/personal-koi/start.sh);
    KOI_PORT is accepted as an alias. Non-numeric values fall back to 8351."""
    for var in ("KOI_API_PORT", "KOI_PORT"):
        raw = (os.getenv(var) or "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                continue
    return 8351


DEFAULT_KOI_PORT = _default_koi_port()
_LOCAL_DB_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1", "[::1]"})


class ScopeFloorError(ValueError):
    """`--scope-enforced-since` could not be resolved to a verified instant."""


@dataclass(frozen=True)
class ServingProcess:
    """What `auto` verifies: the process serving the audited node's KOI API."""
    cwd: pathlib.Path
    pid: int
    started: Optional[datetime]   # aware; None when it could not be read


def _dsn_hosts(dsn: Optional[str], env: Optional[Dict[str, str]] = None) -> List[str]:
    """Every host libpq/asyncpg would consult for `dsn`, lowercased: the URI
    authority, `host=`/`hostaddr=` in the URI query string or in a keyword
    string, and — when the DSN names none — PGHOST / PGHOSTADDR from the
    environment. (asyncpg honours `postgresql:///db?host=…` and PGHOST; the
    earlier version read only the authority and called both 'local' — third
    review A2.) A host beginning with '/' is a Unix-socket directory."""
    env = os.environ if env is None else env
    hosts: List[str] = []
    text = dsn or ""
    parsed = None
    try:
        parsed = urlparse(text)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.scheme and parsed.netloc:
        hosts.append((parsed.hostname or "").lower())
    if parsed is not None and parsed.scheme and parsed.query:
        for key, val in parse_qsl(parsed.query, keep_blank_values=True):
            if key in ("host", "hostaddr"):
                hosts.extend(h.strip().lower() for h in val.split(","))
    for m in re.finditer(r"(?:^|\s)(host|hostaddr)=([^\s]+)", text):
        hosts.extend(h.strip().lower() for h in m.group(2).split(","))
    if not any(h for h in hosts):
        for var in ("PGHOSTADDR", "PGHOST"):
            val = (env.get(var) or "").strip()
            if val:
                hosts.extend(h.strip().lower() for h in val.split(","))
                break
    return hosts


def _dsn_host(dsn: Optional[str]) -> str:
    hosts = [h for h in _dsn_hosts(dsn) if h]
    return hosts[0] if hosts else ""


def dsn_is_local(dsn: Optional[str], env: Optional[Dict[str, str]] = None) -> bool:
    """True only when EVERY host the DSN (or, absent one, PGHOST/PGHOSTADDR)
    would reach is this host or a Unix-socket directory."""
    hosts = _dsn_hosts(dsn, env)
    if not hosts:
        return True   # no host anywhere: libpq's default Unix socket
    return all(h in _LOCAL_DB_HOSTS or h.startswith("/") for h in hosts)


def _run_cmd(argv: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def find_serving_process(port: int) -> ServingProcess:
    """Locate the process listening on `port` and read its cwd and start time
    FROM THE PROCESS (never from a directory layout): /proc/<pid>/cwd on Linux,
    `lsof -d cwd` on macOS; start time from `ps -o lstart=`. Raises
    ScopeFloorError when any step cannot be established."""
    try:
        listeners = _run_cmd(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScopeFloorError(f"cannot run lsof to find the process on :{port}: {exc}") from exc
    pids = sorted({int(x) for x in listeners.stdout.split() if x.strip().isdigit()})
    if not pids:
        raise ScopeFloorError(f"no process is listening on :{port} on this host")
    if len(pids) > 1:
        # Two distinct processes on one port is the documented hazard of a
        # second, launchd-untracked backend racing the service (CLAUDE.md);
        # `auto` cannot know which one served the marks.
        raise ScopeFloorError(
            f"{len(pids)} distinct processes listen on :{port} ({', '.join(map(str, pids))}); "
            f"ambiguous — resolve the duplicate service first")
    pid = pids[0]
    cwd: Optional[pathlib.Path] = None
    proc_cwd = pathlib.Path(f"/proc/{pid}/cwd")
    if proc_cwd.exists():
        try:
            cwd = pathlib.Path(os.readlink(proc_cwd))
        except OSError:
            cwd = None
    if cwd is None:
        try:
            out = _run_cmd(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
        except (OSError, subprocess.SubprocessError) as exc:
            raise ScopeFloorError(f"cannot read the cwd of pid {pid}: {exc}") from exc
        names = [ln[1:] for ln in out.stdout.splitlines() if ln.startswith("n")]
        if not names:
            raise ScopeFloorError(f"cannot read the cwd of pid {pid} (lsof returned nothing)")
        cwd = pathlib.Path(names[-1])
    started: Optional[datetime] = None
    try:
        ps = _run_cmd(["ps", "-p", str(pid), "-o", "lstart="])
        text = ps.stdout.strip()
        if text:
            started = datetime.strptime(text, "%a %b %d %H:%M:%S %Y").astimezone()
    except (OSError, subprocess.SubprocessError, ValueError):
        started = None
    return ServingProcess(cwd=cwd, pid=pid, started=started)


def _git_head_moved_at(root: pathlib.Path) -> Optional[datetime]:
    """When the checkout's HEAD last MOVED: the mtime of the reflog file
    `logs/HEAD`, which git appends to on every checkout, commit, pull, merge or
    reset (the `HEAD` file itself is rewritten only on a branch switch, so a
    fast-forward pull would not show there). Resolved with
    `git rev-parse --git-path`, so linked worktrees read their own reflog.
    Falls back to the `HEAD` file; None if neither is readable."""
    for rel in ("logs/HEAD", "HEAD"):
        try:
            gp = _run_cmd(["git", "-C", str(root), "rev-parse", "--git-path", rel])
        except (OSError, subprocess.SubprocessError):
            return None
        if gp.returncode != 0 or not gp.stdout.strip():
            continue
        path = pathlib.Path(gp.stdout.strip())
        if not path.is_absolute():
            path = root / path
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
    return None


def resolve_scope_floor(
    raw: Optional[str],
    *,
    dsn: Optional[str] = None,
    serving: Optional[ServingProcess] = None,
) -> tuple[Optional[datetime], str]:
    """(floor, how). None → no floor. An ISO instant → as given (preferred).
    `auto` → vouched for ONLY by the serving process of a LOCAL database: `dsn`
    must name this host, `serving` (from `find_serving_process`) must be a
    process whose cwd contains SCOPE_ENFORCEMENT_COMMIT as an ancestor of HEAD
    and whose start time is after the commit; the floor is the commit's
    committer time — a lower bound on enforcement. This file's own checkout is
    never consulted. Anything else raises ScopeFloorError."""
    if raw is None or str(raw).strip() == "":
        return None, "not given: scope_excluded classification disabled"
    text = str(raw).strip()
    if text.lower() != "auto":
        parsed = _parse_carried(text)
        if parsed is None:
            raise ScopeFloorError(f"--scope-enforced-since {raw!r} is neither an ISO timestamp nor 'auto'")
        return parsed, f"given on the command line ({parsed.isoformat()})"
    hint = ("give the instant explicitly: --scope-enforced-since <ISO> = the first enforcing "
            "start of the audited node's service (ps -o lstart= of the serving process, or the "
            "deploy log)")
    if not dsn_is_local(dsn):
        raise ScopeFloorError(
            f"--scope-enforced-since auto: the database host {_dsn_host(dsn)!r} is remote; a local "
            f"process cannot vouch for another node's enforcement — {hint}")
    if serving is None:
        raise ScopeFloorError(f"--scope-enforced-since auto: no serving process to verify — {hint}")
    root = pathlib.Path(serving.cwd)
    try:
        proc = _run_cmd(["git", "-C", str(root), "merge-base", "--is-ancestor", SCOPE_ENFORCEMENT_COMMIT, "HEAD"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScopeFloorError(
            f"--scope-enforced-since auto: cannot run git in the serving checkout {root} (pid "
            f"{serving.pid}): {exc}; commit {SCOPE_ENFORCEMENT_COMMIT} unverifiable — {hint}") from exc
    if proc.returncode != 0:
        raise ScopeFloorError(
            f"--scope-enforced-since auto: commit {SCOPE_ENFORCEMENT_COMMIT} is not an ancestor of "
            f"HEAD in the SERVING checkout {root} (pid {serving.pid}; git exit {proc.returncode}: "
            f"{proc.stderr.strip() or 'no output'}); this node may never have enforced per-edge "
            f"scoping — {hint}, or omit the flag")
    shown = _run_cmd(["git", "-C", str(root), "show", "-s", "--format=%cI %H", SCOPE_ENFORCEMENT_COMMIT])
    if shown.returncode != 0 or not shown.stdout.strip():
        raise ScopeFloorError(
            f"--scope-enforced-since auto: cannot read commit {SCOPE_ENFORCEMENT_COMMIT} in {root}")
    when_s, full_sha = shown.stdout.strip().split(" ", 1)
    when = _parse_carried(when_s)
    if when is None:
        raise ScopeFloorError(f"--scope-enforced-since auto: unparseable commit time {when_s!r}")
    when = when.astimezone(timezone.utc)   # one rendering (UTC) whatever the committer's offset
    if serving.started is None:
        raise ScopeFloorError(
            f"--scope-enforced-since auto: the start time of the serving process (pid {serving.pid}) "
            f"could not be read, so it cannot be shown to postdate the commit — {hint}")
    if serving.started < when:
        raise ScopeFloorError(
            f"--scope-enforced-since auto: the serving process (pid {serving.pid}) started "
            f"{serving.started.isoformat()}, BEFORE commit {SCOPE_ENFORCEMENT_COMMIT} was made "
            f"({when.isoformat()}); it cannot be running that code — {hint}")
    # The checkout's HEAD is what is on disk NOW; the process loaded what was
    # there when it STARTED. If HEAD moved after the start (a branch switch or
    # pull with no restart — the repo's documented 24-hour incident shape), the
    # ancestry of today's HEAD says nothing about the running code (A4).
    head_moved = _git_head_moved_at(root)
    if head_moved is None:
        raise ScopeFloorError(
            f"--scope-enforced-since auto: cannot read when HEAD of the serving checkout {root} "
            f"last moved, so it cannot be shown to predate the process start — {hint}")
    if head_moved > serving.started:
        raise ScopeFloorError(
            f"--scope-enforced-since auto: HEAD of the serving checkout {root} moved at "
            f"{head_moved.isoformat()}, AFTER the serving process (pid {serving.pid}) started "
            f"{serving.started.isoformat()}; the running code is not what is on disk — restart "
            f"the service or {hint}")
    return when, (
        f"auto: {SCOPE_ENFORCEMENT_COMMIT} ({full_sha}, committed {when.isoformat()}) is an ancestor "
        f"of HEAD in the SERVING checkout {root} (pid {serving.pid}, started "
        f"{serving.started.isoformat()}; HEAD unchanged since {head_moved.isoformat()}); the "
        f"floor is the commit time — a lower bound on "
        f"enforcement (events queued between the commit and the first enforcing restart are "
        f"classified as exclusions); prefer an explicit ISO instant")

# Classes that carry an obligation a repair could discharge — IF scope admits.
SENDABLE = frozenset({
    C_POSSIBLY_LIVE, C_UNVERIFIABLE, C_TOMBSTONE_UNCONFIRMED, C_TOMBSTONE_MISMATCH,
    C_PEER_UNMIGRATED, C_SCOPE_FAILED_REOPENABLE,
})
# Classes that make the run "outstanding" (exit 1) even with an empty plan.
# `failed` (attempts exhausted, or a policy failure the edge never reopened)
# and a non-reopenable `rejected` are obligations the transport GAVE UP on —
# outstanding, and listed under "terminal failures" (second review, finding 8).
# `pending` is not: the peer holds no fact, so nothing is live there.
OUTSTANDING = frozenset({
    C_POSSIBLY_LIVE, C_UNVERIFIABLE, C_UNAUTHORIZED, C_TOMBSTONE_MISMATCH, C_HISTORY_UNKNOWN,
    C_PEER_UNMIGRATED, C_SCOPE_FAILED_REOPENABLE, C_FAILED, C_REJECTED,
})
# Classifications whose rows are always terminal. A ledger `rejected` that
# classifies as tombstone_valid_to_mismatch is terminal too — decided per row in
# _classify_peer (`row["terminal"]`), which is what audit() lists.
TERMINAL = frozenset({C_FAILED, C_REJECTED})

EXIT_OK = 0
EXIT_OUTSTANDING = 1
EXIT_APPLY_REFUSED = 2
EXIT_MISCONFIGURED = 3


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_carried(raw: Optional[str]) -> Optional[datetime]:
    """The carried valid_to as an instant; None if absent/null/unparseable.

    A naive value is read as UTC (the recipient's parse_ts does the same), and
    'Z' is accepted. Anything unparseable compares as a mismatch.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------

async def connect_read_only(dsn: str) -> asyncpg.Connection:
    """Connect and make every subsequent transaction on this connection read-only.

    `default_transaction_read_only` governs each BEGIN issued afterwards, so
    the audit's `async with conn.transaction():` inherits it and any write —
    including one from a future edit of this file — raises
    ReadOnlySQLTransactionError. main() additionally asserts
    `SHOW transaction_read_only = on` inside the transaction before reading
    anything, and treats a miss as misconfiguration (exit 3).
    """
    conn = await asyncpg.connect(dsn)
    await conn.execute(READ_ONLY_SQL)
    return conn


async def infer_node_rid(conn: asyncpg.Connection) -> Optional[Dict[str, Any]]:
    """This node's koi-net RID = the majority `source_node` of domain events.

    There is no self row in koi_net_nodes to read. Domain events
    (`contents->>'_koi_domain' IS NOT NULL`) are emitted by this node under its
    own RID, so the majority source is the node — reported with the counts so
    the operator can see how thin the inference was.
    """
    rows = await conn.fetch(
        """
        SELECT source_node, count(*) AS n
        FROM koi_net_events
        WHERE contents->>'_koi_domain' IS NOT NULL AND source_node IS NOT NULL
        GROUP BY source_node
        ORDER BY n DESC
        """
    )
    if not rows:
        return None
    total = sum(int(r["n"]) for r in rows)
    top = rows[0]
    return {
        "node_rid": top["source_node"],
        "rows_from_node": int(top["n"]),
        "rows_total": total,
        "how": (f"inferred: majority source_node of {total} domain-event rows in "
                f"koi_net_events ({int(top['n'])} from this node; "
                f"{len(rows)} distinct sources)"),
    }


# ---------------------------------------------------------------------------
# the audit
# ---------------------------------------------------------------------------

async def _selected_facts(
    conn: asyncpg.Connection,
    fact_ids: Optional[Sequence[str]],
    since: Optional[datetime],
    limit: Optional[int],
) -> List[asyncpg.Record]:
    ids = [UUID(str(x)) for x in fact_ids] if fact_ids else None
    return await conn.fetch(
        """
        SELECT f.id::TEXT AS id, f.episode_id::TEXT AS episode_id, f.subject_uri, f.predicate,
               f.object_uri, f.object_literal, f.valid_from, f.valid_to, f.created_at,
               f.source_node_rid, e.source_document
        FROM knowledge_facts f
        LEFT JOIN knowledge_episodes e ON e.id = f.episode_id
        WHERE f.valid_to IS NOT NULL
          AND ($1::uuid[] IS NULL OR f.id = ANY($1::uuid[]))
          AND ($2::timestamptz IS NULL OR f.valid_to >= $2::timestamptz)
        ORDER BY f.valid_to DESC, f.id
        LIMIT $3
        """,
        ids, since, limit,
    )


async def _carried_rows(conn: asyncpg.Connection) -> List[asyncpg.Record]:
    """(event row id, fact id, carried valid_to) for EVERY retracted fact, one pass.

    Both event shapes that can carry a fact. Restricted by rid prefix (index-
    backed under the C collation both databases use; correct, if slower, under
    any other) and by `_koi_domain`, so a row is never matched on rid alone.
    """
    return await conn.fetch(
        f"""
        WITH retracted AS (
            SELECT id::TEXT AS fid FROM knowledge_facts WHERE valid_to IS NOT NULL
        ),
        carried AS (
            SELECT ev.id AS ev_id, f->>'id' AS fid, f->>'valid_to' AS carried_valid_to
            FROM koi_net_events ev
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(ev.contents->'payload'->'facts') = 'array'
                     THEN ev.contents->'payload'->'facts' ELSE '[]'::jsonb END) f
            WHERE ev.rid LIKE '{EPISODE_PREFIX}%'
              AND ev.contents->>'_koi_domain' = 'knowledge_episode'
            UNION ALL
            SELECT ev.id, ev.contents->'payload'->>'id', ev.contents->'payload'->>'valid_to'
            FROM koi_net_events ev
            WHERE ev.rid LIKE '{FACT_PREFIX}%'
              AND ev.contents->>'_koi_domain' = 'knowledge_fact'
        )
        SELECT c.ev_id, c.fid, c.carried_valid_to
        FROM carried c JOIN retracted r ON r.fid = c.fid
        ORDER BY c.ev_id
        """
    )


async def _event_meta(conn: asyncpg.Connection, ev_ids: Iterable[int]) -> Dict[int, asyncpg.Record]:
    ids = sorted(set(ev_ids))
    if not ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT id, event_id::TEXT AS event_id, event_type, rid, source_node, target_node,
               queued_at, expires_at, delivered_to, confirmed_by,
               contents->>'_koi_domain' AS domain
        FROM koi_net_events WHERE id = ANY($1::int[])
        """,
        ids,
    )
    return {int(r["id"]): r for r in rows}


async def _edges(conn: asyncpg.Connection, node_rid: str) -> Dict[str, Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT target_node, edge_type, status, rid_types, updated_at
        FROM koi_net_edges WHERE source_node = $1
        ORDER BY target_node, updated_at DESC NULLS LAST, id
        """,
        node_rid,
    )
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        peer = r["target_node"]
        entry = out.setdefault(peer, {"edges": [], "edge_status": None, "scope_admits_now": False,
                                      "admitting_scope": None, "approved_scopes": [],
                                      "edges_changed_at": None})
        entry["edges"].append({
            "edge_type": r["edge_type"], "status": r["status"],
            "rid_types": list(r["rid_types"]) if r["rid_types"] is not None else None,
        })
        # The newest change to ANY of this peer's edges (any status): a mark
        # older than this cannot be reclassified from the current scope.
        if r["updated_at"] is not None and (
                entry["edges_changed_at"] is None or r["updated_at"] > entry["edges_changed_at"]):
            entry["edges_changed_at"] = r["updated_at"]
        approved = r["status"] == "APPROVED"
        if approved:
            entry["edge_status"] = "APPROVED"
            entry["approved_scopes"].append(
                list(r["rid_types"]) if r["rid_types"] is not None else None)
            if scope_admits(r["rid_types"]):
                entry["scope_admits_now"] = True
                entry["admitting_scope"] = (
                    list(r["rid_types"]) if r["rid_types"] is not None else None)
        elif entry["edge_status"] is None:
            entry["edge_status"] = r["status"]
    return out


def _is_exclusion_mark(
    event: Dict[str, Any],
    edge: Optional[Dict[str, Any]],
    floor: Optional[datetime],
) -> bool:
    """True when a delivered_to mark on `event` for this peer is PROVABLY a
    poll-filter exclusion (see C_SCOPE_EXCLUDED). Requires: an explicit
    enforcement `floor` (never assumed — None disables the class), the event
    queued after it and after the peer's edges last changed; and no APPROVED
    scope admits the event's domain (a peer with no edge at all cannot have
    polled, so its mark is also an exclusion — but that case never occurs:
    poll() marks only for a polling node with an edge)."""
    if floor is None:
        return False
    q = event.get("queued_at")
    if q is None or q <= floor:
        return False
    if edge is None:
        return False
    changed = edge.get("edges_changed_at")
    if changed is not None and q <= changed:
        return False
    domain = event.get("domain") or fact_retraction.DOMAIN
    return not any(scope_admits(s, domain) for s in edge.get("approved_scopes") or [])


async def _ledger(conn: asyncpg.Connection, fact_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Per fact: its retraction rows and, per (retraction, peer), the delivery row."""
    if not fact_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT r.id AS retraction_id, r.fact_id::TEXT AS fact_id, r.valid_to, r.origin_node,
               r.episode_id::TEXT AS episode_id, r.reason, r.retracted_by, r.source_document,
               r.applied_at, r.created_at,
               d.id AS delivery_id, d.target_node, d.event_id::TEXT AS event_id, d.attempt,
               d.attempt_history, d.state, d.state_reason, d.application,
               d.queued_at, d.delivered_at, d.received_at, d.applied_at AS d_applied_at,
               d.state_changed_at
        FROM knowledge_fact_retractions r
        LEFT JOIN knowledge_fact_retraction_deliveries d ON d.retraction_id = r.id
        WHERE r.fact_id = ANY($1::uuid[])
        ORDER BY r.fact_id, r.id, d.target_node
        """,
        [UUID(x) for x in fact_ids],
    )
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        fact = out.setdefault(r["fact_id"], {"retractions": {}, "deliveries": {}})
        rid = int(r["retraction_id"])
        if rid not in fact["retractions"]:
            fact["retractions"][rid] = {
                "retraction_id": rid, "valid_to": r["valid_to"], "origin_node": r["origin_node"],
                "episode_id": r["episode_id"], "reason": r["reason"],
                "retracted_by": r["retracted_by"], "source_document": r["source_document"],
                "applied_at": r["applied_at"], "created_at": r["created_at"],
            }
        if r["delivery_id"] is not None:
            app = r["application"]
            if isinstance(app, str):
                app = json.loads(app)
            hist = r["attempt_history"]
            if isinstance(hist, str):
                hist = json.loads(hist)
            fact["deliveries"].setdefault(r["target_node"], []).append({
                "retraction_id": rid, "delivery_id": int(r["delivery_id"]),
                "event_id": r["event_id"], "attempt": r["attempt"], "attempt_history": hist,
                "state": r["state"], "state_reason": r["state_reason"], "application": app,
                "queued_at": r["queued_at"], "delivered_at": r["delivered_at"],
                "received_at": r["received_at"], "applied_at": r["d_applied_at"],
                "state_changed_at": r["state_changed_at"],
            })
    return out


def _pick_ledger_delivery(
    fact_ledger: Optional[Dict[str, Any]], peer: str, node_rid: str, local_valid_to: datetime,
) -> Optional[Dict[str, Any]]:
    """The delivery row that governs (fact, peer): this node's retraction with the
    committed valid_to if there is one, else the newest of this node's retractions."""
    if not fact_ledger:
        return None
    rows = fact_ledger["deliveries"].get(peer) or []
    mine = [d for d in rows
            if fact_ledger["retractions"][d["retraction_id"]]["origin_node"] == node_rid]
    if not mine:
        return None
    exact = [d for d in mine
             if fact_ledger["retractions"][d["retraction_id"]]["valid_to"] == local_valid_to]
    chosen = max(exact or mine, key=lambda d: d["retraction_id"])
    ret = fact_ledger["retractions"][chosen["retraction_id"]]
    out = dict(chosen)
    out["retraction_valid_to"] = ret["valid_to"]
    out["valid_to_matches_local"] = ret["valid_to"] == local_valid_to
    return out


def _classify_peer(
    *,
    peer: str,
    history: str,
    edge: Optional[Dict[str, Any]],
    events: List[Dict[str, Any]],
    ledger_row: Optional[Dict[str, Any]],
    now: datetime,
    scope_floor: Optional[datetime] = None,
) -> Dict[str, Any]:
    scope_now = bool(edge and edge["scope_admits_now"])
    live = [e for e in events if e["carried"] == "live_copy"]
    exact = [e for e in events if e["carried"] == "tombstone" and e["valid_to_matches_local"]]
    mism = [e for e in events if e["carried"] == "tombstone" and not e["valid_to_matches_local"]]

    def confirmed(evs):
        return any(peer in (e["confirmed_by"] or []) for e in evs)

    def marked(evs):
        return any(peer in (e["delivered_to"] or []) for e in evs)

    def addressed(evs):
        return any(e["target_node"] == peer for e in evs)

    def pollable(evs):
        # still on the queue for this peer: unexpired, not yet marked, and either
        # unicast to it or broadcast. poll() applies the scope filter either way.
        return any(
            (e["target_node"] is None or e["target_node"] == peer)
            and peer not in (e["delivered_to"] or [])
            and e["expires_at"] is not None and e["expires_at"] > now
            for e in evs)

    live_confirmed = confirmed(live)
    live_marked_only = (not live_confirmed) and (marked(live) or addressed(live))
    tomb_confirmed = confirmed(exact)
    tomb_marked_only = (not tomb_confirmed) and marked(exact)
    tomb_addressed = addressed(exact)
    tomb_in_flight = scope_now and pollable(exact)
    mism_confirmed = confirmed(mism)
    mism_any = mism_confirmed or marked(mism) or addressed(mism)

    row: Dict[str, Any] = {
        "peer": peer,
        "edge_status": edge["edge_status"] if edge else None,
        "edges": edge["edges"] if edge else [],
        "scope_admits_now": scope_now,
        "live_copy_confirmed": live_confirmed,
        "live_copy_delivered_to_only": live_marked_only,
        "tombstone_confirmed": tomb_confirmed,
        "tombstone_delivered_to_only": tomb_marked_only,
        "tombstone_addressed_unicast": tomb_addressed,
        "tombstone_in_flight": tomb_in_flight,
        "tombstone_valid_to_mismatch": mism_any,
        "ledger": None,
        "classification": None,
        "evidence_class": None,
        "application_proven": False,
        "plan_futile": False,
        # True when the ledger holds a verdict the transport cannot move past on
        # its own: `failed`, a non-reopenable `rejected` (any reason, INCLUDING
        # peer_holds_different_valid_to, whose classification stays
        # tombstone_valid_to_mismatch). Every such row is listed under "terminal
        # failures" so it cannot vanish between plan, blocked and terminal
        # (third review, finding N4).
        "terminal": False,
        "note": None,
    }

    if ledger_row is not None:
        row["ledger"] = {
            k: ledger_row[k] for k in (
                "retraction_id", "delivery_id", "event_id", "attempt", "attempt_history",
                "state", "state_reason", "application", "queued_at", "delivered_at",
                "received_at", "applied_at", "state_changed_at", "retraction_valid_to",
                "valid_to_matches_local")
        }

    cls: Optional[str] = None
    note: Optional[str] = None

    if history == "unknown":
        cls = C_HISTORY_UNKNOWN
        note = "no carrying event survives and the fact predates the oldest koi_net_events row"
    elif ledger_row is not None:
        state = ledger_row["state"]
        if state == fact_retraction.STATE_APPLIED:
            cls = C_APPLIED
            note = f"recipient reported application ({ledger_row['state_reason']})"
        elif state == fact_retraction.STATE_PENDING:
            cls = C_PENDING
            note = ("recipient reported the fact ABSENT there and recorded a pending tombstone; "
                    "nothing live to repair, application not proven "
                    f"({ledger_row['state_reason']})")
        elif state == fact_retraction.STATE_REJECTED:
            reason = ledger_row["state_reason"] or ""
            if reason.startswith(fact_retraction.REOPENABLE_REJECT_PREFIX):
                cls = C_PEER_UNMIGRATED
                note = (f"recipient rejected: {reason} — it has not applied migration 127; "
                        f"the sweep re-queues this, apply 127 there first")
            elif reason == "peer_holds_different_valid_to":
                # Unlike a transport-evidence mismatch (a confirmed tombstone
                # event with the wrong value, which a re-send corrects on old
                # code and shortens on new code), this is a NEW-code recipient
                # that already refused: it keeps its earlier tombstone by
                # design. Re-sending is futile — visible, outstanding, no plan.
                cls = C_TOMBSTONE_MISMATCH
                row["plan_futile"] = True
                note = ("recipient keeps an EARLIER tombstone with a different valid_to "
                        "(report in `application`); re-sending cannot change it")
            else:
                cls = C_REJECTED
                note = f"recipient rejected: {reason}"
        elif state == fact_retraction.STATE_FAILED:
            reason = ledger_row["state_reason"] or ""
            if reason in fact_retraction.SCOPE_FAIL_REASONS and scope_now:
                cls = C_SCOPE_FAILED_REOPENABLE
                note = f"failed for scope ({reason}) but the edge admits again; the sweep re-queues this"
            else:
                cls = C_FAILED
                note = f"terminal: {ledger_row['state_reason']}"
        elif state == fact_retraction.STATE_UNVERIFIABLE:
            cls = C_UNVERIFIABLE
            note = f"ledger: {ledger_row['state_reason']}"
        elif state == fact_retraction.STATE_RECEIVED:
            cls = C_TOMBSTONE_CONFIRMED
            note = "ledger: peer confirmed receipt; no application report yet"
        elif state in fact_retraction.OPEN_STATES:
            cls = C_TOMBSTONE_UNCONFIRMED
            note = f"ledger: {state} ({ledger_row['state_reason']})"
            if not tomb_in_flight and ledger_row.get("event_id"):
                # the ledger's own event row decides in-flight when the transport
                # rows above did not already say so
                for e in exact:
                    if e["event_id"] == ledger_row["event_id"] and e["expires_at"] and e["expires_at"] > now:
                        tomb_in_flight = scope_now
                        row["tombstone_in_flight"] = tomb_in_flight
        elif state == fact_retraction.STATE_UNAUTHORIZED:
            # policy at retraction time. If the edge admits NOW, the obligation is
            # sendable; fall through to the transport evidence below.
            note = f"ledger: unauthorized ({ledger_row['state_reason']})"
            cls = None

    if cls is None:
        if tomb_confirmed:
            cls = C_TOMBSTONE_CONFIRMED
            note = note or "peer confirmed receipt of an exact tombstone; application unproven"
        elif mism_confirmed:
            cls = C_TOMBSTONE_MISMATCH
            note = note or "peer confirmed a tombstone carrying a DIFFERENT valid_to"
        elif tomb_addressed or tomb_in_flight or (tomb_marked_only and live_confirmed):
            # A delivered_to mark on the tombstone is evidence of nothing by
            # itself (it is set for scope-excluded events too). It counts as
            # an unconfirmed hand-over only when the peer provably held the
            # live copy — then a mark with no receipt is the apply-failed
            # shape koi_poller leaves behind.
            cls = C_TOMBSTONE_UNCONFIRMED
            if tomb_in_flight:
                note = note or "exact tombstone still pollable by this peer"
            elif tomb_addressed:
                note = note or "exact tombstone unicast to this peer; no receipt"
            else:
                note = note or ("peer confirmed the live copy; the exact tombstone carries a "
                                "delivered_to mark but no receipt")
        elif live_confirmed:
            cls = C_POSSIBLY_LIVE
            note = note or "peer confirmed receipt of the live copy; no tombstone evidence"
        elif live_marked_only:
            marked_live = [e for e in live if peer in (e["delivered_to"] or [])]
            if marked_live and not addressed(live) and all(
                    _is_exclusion_mark(e, edge, scope_floor) for e in marked_live):
                cls = C_SCOPE_EXCLUDED
                note = note or ("every delivered_to mark on the live copy is a provable poll-filter "
                                "exclusion (event queued after per-edge scoping and after this "
                                "peer's edges last changed; no approved scope admits the domain) — "
                                "the peer was never handed the fact")
            else:
                cls = C_UNVERIFIABLE
                note = note or ("live copy carries a delivered_to mark only — hand-over or scope "
                                "exclusion, indistinguishable here; no tombstone receipt"
                                + ("; the tombstone's delivered_to mark is equally uninformative"
                                   if tomb_marked_only else "")
                                + ("; no --scope-enforced-since floor given, so a provable "
                                   "exclusion cannot be told apart" if scope_floor is None else ""))
        else:
            cls = C_NEVER_SENT
            note = note or "no evidence this peer was ever handed the live copy"

    if cls in SENDABLE and not scope_now:
        row["evidence_class"] = cls
        cls = C_UNAUTHORIZED
        why = "no edge from this node" if edge is None else (
            f"edge {edge['edge_status']} does not admit knowledge_fact")
        note = f"{why}; evidence: {row['evidence_class']} — {note}"

    row["classification"] = cls
    row["application_proven"] = cls == C_APPLIED
    row["note"] = note
    # Decided from the LEDGER verdict, not from the (possibly rewritten)
    # classification: a `rejected: peer_holds_different_valid_to` on a peer whose
    # edge has since been narrowed classifies `unauthorized` (evidence_class
    # tombstone_valid_to_mismatch) and must still be listed as terminal, not
    # filed under blocked as a widen-the-edge decision that could not help
    # (third review A1).
    ledger_state = ledger_row["state"] if ledger_row is not None else None
    ledger_reason = (ledger_row["state_reason"] or "") if ledger_row is not None else ""
    row["terminal"] = (
        cls in TERMINAL
        or (ledger_state == fact_retraction.STATE_REJECTED
            and not ledger_reason.startswith(fact_retraction.REOPENABLE_REJECT_PREFIX))
        or (ledger_state == fact_retraction.STATE_FAILED
            and not (ledger_reason in fact_retraction.SCOPE_FAIL_REASONS and scope_now))
    )
    return row


async def audit(
    conn: asyncpg.Connection,
    node_rid: str,
    *,
    fact_ids: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
    scope_enforced_since: Optional[datetime] = None,
    scope_enforced_since_source: Optional[str] = None,
) -> Dict[str, Any]:
    """The audit as a dict (JSON-able through `render_json`). Pure read on `conn`.

    `scope_enforced_since` enables the `scope_excluded` class (see the module
    docstring). None = disabled."""
    t0 = time.monotonic()
    now: datetime = await conn.fetchval("SELECT NOW()")
    ledger_ok = await fact_retraction.ledger_available(conn)

    selected = await _selected_facts(conn, fact_ids, since, limit)
    sel_ids = [r["id"] for r in selected]

    carried = await _carried_rows(conn)
    carried_by_fact: Dict[str, List[asyncpg.Record]] = {}
    for c in carried:
        carried_by_fact.setdefault(c["fid"], []).append(c)

    population_total = int(await conn.fetchval(
        "SELECT count(*) FROM knowledge_facts WHERE valid_to IS NOT NULL"))
    population_with_history = len(carried_by_fact)
    oldest_event = await conn.fetchval("SELECT min(queued_at) FROM koi_net_events")
    events_total = int(await conn.fetchval("SELECT count(*) FROM koi_net_events"))

    ev_meta = await _event_meta(
        conn, (int(c["ev_id"]) for fid in sel_ids for c in carried_by_fact.get(fid, [])))
    edges = await _edges(conn, node_rid)
    ledger = await _ledger(conn, sel_ids) if ledger_ok else {}

    facts_out: List[Dict[str, Any]] = []
    plan: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    terminal: List[Dict[str, Any]] = []
    by_class: Counter = Counter()
    by_history: Counter = Counter()

    for f in selected:
        fid = f["id"]
        local_valid_to: datetime = f["valid_to"]
        local_iso = local_valid_to.isoformat()
        assert local_iso == _iso(local_valid_to)

        events: List[Dict[str, Any]] = []
        for c in carried_by_fact.get(fid, []):
            m = ev_meta.get(int(c["ev_id"]))
            if m is None:
                continue
            carried_dt = _parse_carried(c["carried_valid_to"])
            is_tomb = c["carried_valid_to"] is not None
            events.append({
                "event_row_id": int(m["id"]), "event_id": m["event_id"],
                "event_type": m["event_type"], "domain": m["domain"], "rid": m["rid"],
                "source_node": m["source_node"], "target_node": m["target_node"],
                "queued_at": m["queued_at"], "expires_at": m["expires_at"],
                "expired": bool(m["expires_at"] is not None and m["expires_at"] <= now),
                "delivered_to": list(m["delivered_to"] or []),
                "confirmed_by": list(m["confirmed_by"] or []),
                "carried": "tombstone" if is_tomb else "live_copy",
                "carried_valid_to": c["carried_valid_to"],
                "valid_to_matches_local": bool(is_tomb and carried_dt is not None
                                               and carried_dt == local_valid_to),
            })
        events.sort(key=lambda e: (e["queued_at"] or now, e["event_row_id"]))

        if events:
            history = "known"
        elif oldest_event is not None and f["created_at"] is not None and f["created_at"] < oldest_event:
            history = "unknown"
        else:
            history = "none"
        by_history[history] += 1

        peers: set = set()
        for e in events:
            peers.update(e["delivered_to"])
            peers.update(e["confirmed_by"])
            if e["target_node"]:
                peers.add(e["target_node"])
        peers.update(edges.keys())
        peers.discard(node_rid)

        fact_ledger = ledger.get(fid)
        peer_rows: List[Dict[str, Any]] = []
        for peer in sorted(peers):
            ledger_row = _pick_ledger_delivery(fact_ledger, peer, node_rid, local_valid_to)
            row = _classify_peer(
                peer=peer, history=history, edge=edges.get(peer), events=events,
                ledger_row=ledger_row, now=now, scope_floor=scope_enforced_since)
            peer_rows.append(row)
            by_class[row["classification"]] += 1
            if row["terminal"]:
                ledger_state = (row["ledger"] or {}).get("state")
                terminal.append({
                    "fact_id": fid, "peer": peer, "state": row["classification"],
                    "ledger_state": ledger_state,
                    "state_reason": (row["ledger"] or {}).get("state_reason"),
                    "attempt": (row["ledger"] or {}).get("attempt"),
                    "valid_to": local_iso,
                    "text": (f"terminal {row['classification']} (ledger {ledger_state}): {peer} "
                             f"for fact {fid} ({(row['ledger'] or {}).get('state_reason')}; "
                             f"attempt {(row['ledger'] or {}).get('attempt')})"),
                })

            if row["classification"] in SENDABLE and not row["plan_futile"]:
                if row["classification"] == C_TOMBSTONE_UNCONFIRMED and row["tombstone_in_flight"]:
                    live_ev = next((e for e in events if e["carried"] == "tombstone"
                                    and e["valid_to_matches_local"] and not e["expired"]
                                    and (e["target_node"] in (None, peer))), None)
                    plan.append({
                        "action": "wait", "fact_id": fid, "peer": peer,
                        "domain": fact_retraction.DOMAIN, "event_type": fact_retraction.EVENT_TYPE,
                        "valid_to": local_iso, "classification": row["classification"],
                        "event_id": live_ev["event_id"] if live_ev else None,
                        "expires_at": live_ev["expires_at"] if live_ev else None,
                        "text": (f"wait: exact tombstone event {live_ev['event_id'] if live_ev else '?'} "
                                 f"to {peer} for fact {fid} is still pollable "
                                 f"(expires {_iso(live_ev['expires_at']) if live_ev else '?'})"),
                    })
                else:
                    suffix = ""
                    if row["classification"] == C_PEER_UNMIGRATED:
                        suffix = " (peer reported ledger_unavailable — apply migration 127 there first)"
                    elif row["classification"] == C_SCOPE_FAILED_REOPENABLE:
                        suffix = " (edge admits again after a scope failure)"
                    plan.append({
                        "action": "would_queue", "fact_id": fid, "peer": peer,
                        "domain": fact_retraction.DOMAIN, "event_type": fact_retraction.EVENT_TYPE,
                        "valid_to": local_iso, "classification": row["classification"],
                        "text": (f"would queue {fact_retraction.DOMAIN} {fact_retraction.EVENT_TYPE} "
                                 f"retraction event to {peer} for fact {fid} "
                                 f"carrying valid_to={local_iso}{suffix}"),
                    })
            elif row["classification"] == C_UNAUTHORIZED:
                blocked.append({
                    "fact_id": fid, "peer": peer, "evidence_class": row["evidence_class"],
                    "edge_status": row["edge_status"], "valid_to": local_iso,
                    "text": (f"cannot send under current policy — operator decision: {peer} "
                             f"for fact {fid} (evidence: {row['evidence_class']}; "
                             f"edge: {row['edge_status'] or 'none'})"),
                })

        fact_out: Dict[str, Any] = {
            "fact_id": fid, "episode_id": f["episode_id"], "subject_uri": f["subject_uri"],
            "predicate": f["predicate"], "object_uri": f["object_uri"],
            "object_literal": f["object_literal"], "valid_from": f["valid_from"],
            "valid_to": local_valid_to, "valid_to_iso": local_iso, "created_at": f["created_at"],
            # A future valid_to is a validity interval set at creation (TRAVELS_TO,
            # SCHEDULED_FOR, ...), not a retraction. Retrieval hides both alike
            # (`valid_to IS NULL`), and a peer holding a different valid_to for
            # such a fact is the same byte-faithfulness question, so it is
            # audited — but labelled so nobody counts it as a retraction.
            "valid_to_in_future": bool(local_valid_to > now),
            "source_node_rid": f["source_node_rid"], "source_document": f["source_document"],
            "history": history,
            "events": events,
            "peers": peer_rows,
            "ledger": None,
        }
        if ledger_ok:
            fact_out["ledger"] = {
                "retractions": [
                    dict(r, valid_to_matches_local=(r["valid_to"] == local_valid_to))
                    for r in (fact_ledger["retractions"].values() if fact_ledger else [])
                ],
                "deliveries": (fact_ledger["deliveries"] if fact_ledger else {}),
            }
        facts_out.append(fact_out)

    outstanding = bool(plan) or any(by_class[c] for c in OUTSTANDING)
    summary = {
        "selected": len(facts_out),
        "selected_with_future_valid_to": sum(1 for x in facts_out if x["valid_to_in_future"]),
        "population": {
            "retracted_total": population_total,
            "retracted_with_history": population_with_history,
            "retracted_without_history": population_total - population_with_history,
            "koi_net_events_total": events_total,
            "oldest_event_queued_at": oldest_event,
        },
        "facts_by_history": dict(by_history),
        "by_classification": dict(by_class),
        "plan_would_queue": sum(1 for p in plan if p["action"] == "would_queue"),
        "plan_wait": sum(1 for p in plan if p["action"] == "wait"),
        "blocked": len(blocked),
        "terminal_failures": terminal,
        "outstanding": outstanding,
        "outstanding_is_selection_scoped": bool(fact_ids or since or limit is not None),
        "exit_code": EXIT_OUTSTANDING if outstanding else EXIT_OK,
        "elapsed_s": round(time.monotonic() - t0, 3),
    }
    # JSON-native throughout: every timestamp is `.isoformat()` of the DB value,
    # so an in-process caller and a `--json` consumer read identical strings.
    return _jsonable({
        "generated_at": now,
        "node_rid": node_rid,
        "ledger_available": ledger_ok,
        "scope_enforced_since": scope_enforced_since,
        "scope_enforced_since_source": scope_enforced_since_source
            or ("given" if scope_enforced_since else "not given: scope_excluded classification disabled"),
        "filters": {"fact_ids": list(fact_ids) if fact_ids else None,
                    "since": since, "limit": limit},
        "facts": facts_out,
        "plan": plan,
        "blocked": blocked,
        "summary": summary,
    })


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(_jsonable(report), indent=2, sort_keys=False)


def render_human(report: Dict[str, Any]) -> str:
    out: List[str] = []
    w = out.append
    s = report["summary"]
    pop = s["population"]
    w("audit_fact_retractions — READ-ONLY (" + READ_ONLY_SQL + "); nothing was written")
    w(f"generated_at: {report['generated_at']}")
    w(f"node_rid: {report['node_rid']}")
    if report.get("node_rid_source"):
        w(f"node_rid source: {report['node_rid_source']}")
    if report["ledger_available"]:
        w("ledger: present (migration 127 applied)")
    else:
        w("ledger: absent (migration 127 not applied) — no `applied` verdict is possible; "
          "classification uses koi_net_events evidence only")
    w(f"scope enforced since: {report.get('scope_enforced_since') or 'NOT GIVEN'} "
      f"({report.get('scope_enforced_since_source')})")
    w(f"population: {pop['retracted_total']} locally retracted facts; "
      f"{pop['retracted_with_history']} carried by at least one surviving koi_net_events row; "
      f"{pop['retracted_without_history']} with no surviving history "
      f"(koi_net_events rows: {pop['koi_net_events_total']}, oldest queued_at "
      f"{pop['oldest_event_queued_at']})")
    flt = report["filters"]
    w(f"selected: {s['selected']} (fact_ids={len(flt['fact_ids']) if flt['fact_ids'] else '-'}, "
      f"since={flt['since'] or '-'}, limit={flt['limit'] if flt['limit'] is not None else '-'})")
    w("")
    for f in report["facts"]:
        future = "  (valid_to is in the FUTURE: a validity interval, not a retraction)" \
            if f["valid_to_in_future"] else ""
        w(f"fact {f['fact_id']}  valid_to={f['valid_to_iso']}  history={f['history']}{future}")
        w(f"  episode={f['episode_id']}  source_document={f['source_document']}")
        obj = f["object_uri"] if f["object_uri"] is not None else repr(f["object_literal"])
        w(f"  {f['subject_uri']} --{f['predicate']}--> {obj}")
        if f["events"]:
            w(f"  events ({len(f['events'])}):")
            for e in f["events"]:
                carried = "live_copy" if e["carried"] == "live_copy" else (
                    f"tombstone valid_to={e['carried_valid_to']} "
                    f"matches_local={'yes' if e['valid_to_matches_local'] else 'NO'}")
                w(f"    {e['event_type']:<7} {e['event_id']}  domain={e['domain']}  {carried}")
                w(f"            queued={e['queued_at']}  expires={e['expires_at']}"
                  f"{'  EXPIRED' if e['expired'] else ''}  target={e['target_node'] or 'broadcast'}")
                w(f"            delivered_to (includes scope-exclusions; not evidence): "
                  f"{len(e['delivered_to'])} {e['delivered_to']}")
                w(f"            confirmed_by (receipt only): {len(e['confirmed_by'])} {e['confirmed_by']}")
        else:
            w("  events: none survive")
        w(f"  peers ({len(f['peers'])}):")
        for p in f["peers"]:
            led = f"  ledger={p['ledger']['state']}" if p["ledger"] else ""
            ev = f" (evidence: {p['evidence_class']})" if p["evidence_class"] else ""
            w(f"    {p['peer']}")
            w(f"        edge={p['edge_status'] or 'none'} scope_admits_now={'yes' if p['scope_admits_now'] else 'no'}"
              f"{led}  -> {p['classification']}{ev}")
            w(f"        {p['note']}")
        w("")
    w("plan (dry run — nothing was written; --apply is not implemented in this branch):")
    if report["plan"]:
        for p in report["plan"]:
            w(f"  {p['text']}")
    else:
        w("  (empty)")
    w("blocked (cannot send under current policy — operator decision):")
    if report["blocked"]:
        for b in report["blocked"]:
            w(f"  {b['text']}")
    else:
        w("  (none)")
    w("terminal failures (the transport gave up, or the recipient refused by design — "
      "incl. rejected: peer_holds_different_valid_to; outstanding, no plan line):")
    if s.get("terminal_failures"):
        for tf in s["terminal_failures"]:
            w(f"  {tf['text']}")
    else:
        w("  (none)")
    w("")
    w("summary:")
    w(f"  facts selected: {s['selected']}  by history: {s['facts_by_history']}  "
      f"with future valid_to (validity intervals, not retractions): {s['selected_with_future_valid_to']}")
    w(f"  (fact, peer) by classification: {dict(sorted(s['by_classification'].items()))}")
    w(f"  plan: would_queue={s['plan_would_queue']} wait={s['plan_wait']}  blocked={s['blocked']}  "
      f"terminal failures={len(s.get('terminal_failures') or [])}")
    w(f"  outstanding: {'yes' if s['outstanding'] else 'no'}"
      f"{' (selection-scoped: filters given)' if s.get('outstanding_is_selection_scoped') else ''}"
      f"  exit_code: {s['exit_code']}  elapsed: {s['elapsed_s']}s")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="audit_fact_retractions.py",
        description=("Issue #67: read-only audit of locally retracted facts against federation "
                     "history, with a dry-run repair plan."))
    p.add_argument("--dsn", default=DEFAULT_DSN,
                   help="PostgreSQL DSN (default: $POSTGRES_URL, else the personal_koi default)")
    p.add_argument("--node-rid", default=None,
                   help="this node's koi-net RID; inferred from koi_net_events when absent")
    p.add_argument("--fact-id", action="append", default=None, metavar="UUID",
                   help="restrict to this fact (repeatable)")
    p.add_argument("--since", default=None, metavar="ISO",
                   help="only facts with valid_to >= this timestamp")
    p.add_argument("--limit", type=int, default=None, help="at most N facts (newest valid_to first)")
    p.add_argument("--scope-enforced-since", default=None, metavar="ISO|auto",
                   help=("enable the scope_excluded class: the instant this node's SERVICE began "
                         "enforcing per-edge scoping (ISO, preferred — naive values are read as UTC), "
                         "or 'auto' (local database only) to verify that the process listening on "
                         "--koi-port runs a checkout containing commit 2c497f0 and started after it, "
                         "using the commit time as a lower bound; absent = class disabled"))
    p.add_argument("--koi-port", type=int, default=DEFAULT_KOI_PORT,
                   help=("port of this host's KOI API, used by --scope-enforced-since auto to find "
                         f"the serving process (default $KOI_PORT or 8351; now {DEFAULT_KOI_PORT})"))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--apply", action="store_true",
                   help="REFUSED: repair application is a #67 follow-up, not implemented here")
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    since: Optional[datetime] = None
    if args.since:
        since = _parse_carried(args.since)
        if since is None:
            print(f"error: --since {args.since!r} is not an ISO timestamp", file=sys.stderr)
            return EXIT_MISCONFIGURED
    if args.fact_id:
        for raw in args.fact_id:
            try:
                UUID(raw)
            except ValueError:
                print(f"error: --fact-id {raw!r} is not a UUID", file=sys.stderr)
                return EXIT_MISCONFIGURED
    try:
        serving: Optional[ServingProcess] = None
        if (args.scope_enforced_since or "").strip().lower() == "auto":
            if not dsn_is_local(args.dsn):
                raise ScopeFloorError(
                    f"--scope-enforced-since auto: the database host {_dsn_host(args.dsn)!r} is remote; "
                    f"a local process cannot vouch for another node's enforcement — give the instant "
                    f"explicitly (--scope-enforced-since <ISO>)")
            try:
                serving = find_serving_process(args.koi_port)
            except ScopeFloorError as exc:
                raise ScopeFloorError(
                    f"--scope-enforced-since auto: {exc}; the serving process could not be verified — "
                    f"give the instant explicitly (--scope-enforced-since <ISO>)") from exc
        floor, floor_how = resolve_scope_floor(args.scope_enforced_since, dsn=args.dsn, serving=serving)
    except ScopeFloorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MISCONFIGURED

    try:
        conn = await connect_read_only(args.dsn)
    except (OSError, asyncpg.PostgresError, ValueError) as exc:
        print(f"error: cannot connect to {args.dsn!r}: {exc}", file=sys.stderr)
        return EXIT_MISCONFIGURED

    try:
        async with conn.transaction():
            if await conn.fetchval("SHOW transaction_read_only") != "on":
                print("error: transaction is not read-only; refusing to run", file=sys.stderr)
                return EXIT_MISCONFIGURED
            node_rid = args.node_rid
            source = "given on the command line"
            if not node_rid:
                inferred = await infer_node_rid(conn)
                if not inferred:
                    print("error: --node-rid not given and koi_net_events holds no domain events "
                          "to infer it from", file=sys.stderr)
                    return EXIT_MISCONFIGURED
                node_rid = inferred["node_rid"]
                source = inferred["how"]
            report = await audit(conn, node_rid, fact_ids=args.fact_id, since=since, limit=args.limit,
                                 scope_enforced_since=floor, scope_enforced_since_source=floor_how)
            report["node_rid_source"] = source
            report["read_only"] = True
    finally:
        await conn.close()

    if args.json:
        print(render_json(report))
    else:
        print(render_human(report), end="")
    return int(report["summary"]["exit_code"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.apply:
        print("--apply refused: repair application is a #67 follow-up and is not implemented "
              "in this branch. Run without --apply for the read-only dry-run plan.",
              file=sys.stderr)
        return EXIT_APPLY_REFUSED
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
