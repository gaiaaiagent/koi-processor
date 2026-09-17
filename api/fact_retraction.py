"""Issue #67 — durable, recipient-scoped knowledge-fact retractions.

WHAT THIS MODULE OWNS
---------------------
The retraction OBLIGATION and its evidence, on both sides of a federation edge.

Publisher (the node that retracts):
  `retract_fact_transactional` — one transaction: lock the fact, write
  `valid_to = NOW()`, snapshot the committed row, write the tombstone-ledger
  row, and queue ONE unicast `knowledge_fact` UPDATE event per authorized
  recipient through `EventQueue.add(conn=conn)`. Nothing in this path is
  post-commit or best-effort; if any step fails the whole retraction rolls
  back and the caller sees the exception.
  `record_deliveries` / `record_receipts` / `record_applications` — the three
  transport observations that move a delivery through its states.
  `plan_requeue` / `apply_requeue` / `sweep_once` — retry after expiry,
  terminal failure after `max_attempts`, `unverifiable` for receipt without a
  report.

Recipient:
  `record_inbound_tombstone` — the ledger row for a tombstone that arrived,
  applied or pending. `apply_pending_tombstones` — consulted by the fact
  insert paths so UPDATE-before-NEW cannot land a retracted fact live.

Both:
  `tombstone_status` — the authorized UUID lookup. Ordinary retrieval keeps
  hiding retracted facts; this surface proves the tombstone.

WHAT IT DOES NOT CLAIM
----------------------
`applied` is the RECIPIENT'S report, carried back in the koi-net confirm
payload (`applications`). A peer running older code reports nothing, so its
deliveries stop at `received` and age into `unverifiable`. A peer that does
not hold the fact reports `pending` — recorded as its own state, never as
`applied` — and nothing on this side learns when the fact later arrives
there. Proving application on any peer needs the signed lookup slice
(`/koi-net/facts/lookup`), which the publisher may call but which this
session never runs against a live peer.

AUTHENTICATION. The ledger is written only from SIGNED koi-net envelopes:
an unsigned poll is not handed retraction events (`EventQueue.poll`,
`authenticated=False`), and an unsigned confirm records neither receipt nor
application here. This holds regardless of KOI_REQUIRE_SIGNED_ENVELOPES.

WIRE SHAPE (domain `knowledge_fact`, event_type `UPDATE`)
---------------------------------------------------------
payload = the knowledge_facts row snapshot in the exact key set
`_insert_fact` reads (id, episode_id, subject_uri, predicate, object_uri,
object_literal, fact_text, valid_from, valid_to, created_at, group_id,
source_node_rid, turn_range_start, turn_range_end, embedding_column,
embedding_value) PLUS a `retraction` block:

    {"retraction_id": int, "origin_node": str, "valid_to": iso,
     "reason": str|None, "retracted_by": str|None, "attempt": int,
     "episode_rid": str, "source_document": str|None,
     "original_event_ids": [str, ...]}

A recipient on OLD code ignores `retraction` and applies the row through
`_insert_fact`'s upsert — which sets `valid_to` — so the tombstone still
lands where the episode exists. A recipient on NEW code routes on the block
(`domain_event_handlers._apply_knowledge_fact`).

`valid_to` in the payload is `row["valid_to"].isoformat()` of the value the
UPDATE ... RETURNING committed. It is never constructed from the clock.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence
from uuid import UUID, uuid4

import asyncpg

logger = logging.getLogger(__name__)

DOMAIN = "knowledge_fact"
EVENT_TYPE = "UPDATE"
FACT_RID_PREFIX = "orn:personal-koi.knowledge-fact:"
EPISODE_RID_PREFIX = "orn:personal-koi.knowledge-episode:"

# Retraction events use the remote TTL: an obligation to a peer that is
# offline for a weekend should still be pollable on Monday.
RETRACTION_TTL_HOURS = 72
DEFAULT_MAX_ATTEMPTS = 5

# The delivery state vocabulary. Mirrored by the CHECK constraint in
# migrations/127_fact_retraction_ledger.sql (asserted there, and in
# tests/test_migration_127.py, from a literal list — not from this set).
STATE_QUEUED = "queued"
STATE_DELIVERED = "delivered"
STATE_RECEIVED = "received"
STATE_APPLIED = "applied"
# The recipient reported `pending`: it holds NO fact row, only a pending
# tombstone (applied_at NULL) that its own apply paths land when the fact
# arrives. Not application — the fact is absent there — and not terminal: a
# later signed report (a re-sent event answered `already_tombstoned` with the
# same valid_to once the fact has landed) may still advance it to `applied`.
# The sweep leaves it alone: there is nothing live to retract and nothing to
# retry. Cross-node proof needs `POST /koi-net/facts/lookup` on that peer.
STATE_PENDING = "pending"
STATE_REJECTED = "rejected"
STATE_RETRYING = "retrying"
STATE_FAILED = "failed"
STATE_UNVERIFIABLE = "unverifiable"
STATE_UNAUTHORIZED = "unauthorized"
STATES = frozenset({
    STATE_QUEUED, STATE_DELIVERED, STATE_RECEIVED, STATE_APPLIED, STATE_PENDING,
    STATE_REJECTED, STATE_RETRYING, STATE_FAILED, STATE_UNVERIFIABLE, STATE_UNAUTHORIZED,
})
# States a later application report may still move (everything the recipient
# has not given a verdict on). `applied`/`rejected`/`failed`/`unauthorized`
# keep their first verdict.
REPORTABLE_STATES = frozenset({
    STATE_QUEUED, STATE_DELIVERED, STATE_RETRYING, STATE_RECEIVED, STATE_PENDING,
    STATE_UNVERIFIABLE,
})
TERMINAL_STATES = frozenset({STATE_APPLIED, STATE_REJECTED, STATE_FAILED, STATE_UNAUTHORIZED})
# States in which the publisher is still waiting on the transport.
OPEN_STATES = frozenset({STATE_QUEUED, STATE_DELIVERED, STATE_RETRYING})
# Terminal-looking states the sweep may REOPEN (review findings P2/P3):
#   rejected + a reason starting with this prefix = the peer has not applied
#   migration 127 (an infrastructure condition, not a verdict on the fact);
#   failed + one of these reasons = a policy change that the operator may
#   reverse by widening the edge again.
REOPENABLE_REJECT_PREFIX = "ledger_unavailable"
SCOPE_FAIL_REASONS = frozenset({
    "edge_scope_excluded_at_poll", "edge_no_longer_admits_knowledge_fact",
})
# Recipient report statuses (the `status` key of an application report).
REPORT_APPLIED = "applied"
REPORT_ALREADY_TOMBSTONED = "already_tombstoned"
REPORT_PENDING = "pending"
REPORT_REJECTED = "rejected"
REPORT_STATUSES = frozenset({
    REPORT_APPLIED, REPORT_ALREADY_TOMBSTONED, REPORT_PENDING, REPORT_REJECTED,
})

# origin_node when the node has no federation identity at all.
LOCAL_ORIGIN = "local"

# apply_pending_tombstones warns once per process when the ledger is absent.
_warned_ledger_absent = False
# retract_fact_transactional(on_missing_ledger="skip") warns once per process.
_warned_ledger_absent_publisher = False

_SNAPSHOT_KEYS = (
    "id", "episode_id", "subject_uri", "predicate", "object_uri", "object_literal",
    "fact_text", "valid_from", "valid_to", "created_at", "group_id",
    "source_node_rid", "turn_range_start", "turn_range_end",
)


class LedgerUnavailable(RuntimeError):
    """migration 127 is not applied on this database."""


class RetractionError(RuntimeError):
    """A retraction could not be recorded durably; nothing was committed."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def fact_rid(fact_id: str | UUID) -> str:
    return f"{FACT_RID_PREFIX}{fact_id}"


def episode_rid(episode_id: str | UUID) -> str:
    return f"{EPISODE_RID_PREFIX}{episode_id}"


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def snapshot_from_row(row: Any) -> Dict[str, Any]:
    """Serialize a knowledge_facts row into the `_insert_fact` payload shape.

    Timestamps are `.isoformat()` of the committed values; the embedding is
    deliberately NOT carried (a retraction never needs to re-embed anything,
    and the recipient's upsert only touches valid_to on conflict).
    """
    snap: Dict[str, Any] = {}
    for k in _SNAPSHOT_KEYS:
        snap[k] = _jsonable(row[k]) if k in row.keys() else None
    snap["embedding_column"] = None
    snap["embedding_value"] = None
    return snap


def scope_admits(rid_types: Optional[Sequence[str]], domain: str = DOMAIN) -> bool:
    """Mirror of `EventQueue.poll`'s domain matching.

    poll() is handed the edge's `rid_types` verbatim: None means "no filter
    declared" (everything flows), a list means the lowercased domain name must
    be a member. Recipient selection has to agree with the transport filter
    exactly, or a queued event is marked delivered-and-excluded at the peer's
    next poll — the very artifact #67 is about. Pinned against a real poll in
    tests/test_fact_retraction_outbox.py.
    """
    if rid_types is None:
        return True
    lowered = {str(x).lower() for x in rid_types}
    return domain.lower() in lowered


async def ledger_available(conn: asyncpg.Connection) -> bool:
    row = await conn.fetchrow(
        "SELECT to_regclass('knowledge_fact_retractions') AS a, "
        "       to_regclass('knowledge_fact_retraction_deliveries') AS b"
    )
    return row["a"] is not None and row["b"] is not None


async def require_ledger(conn: asyncpg.Connection) -> None:
    if not await ledger_available(conn):
        raise LedgerUnavailable(
            "knowledge_fact_retractions / knowledge_fact_retraction_deliveries are "
            "missing — apply migrations/127_fact_retraction_ledger.sql")


# ---------------------------------------------------------------------------
# Recipient selection
# ---------------------------------------------------------------------------

async def _admitting_peers(conn: asyncpg.Connection, node_rid: str) -> set[str]:
    """Peers with an APPROVED outbound edge from `node_rid` whose scope admits knowledge_fact."""
    edges = await conn.fetch(
        """
        SELECT target_node, rid_types
        FROM koi_net_edges
        WHERE source_node = $1 AND status = 'APPROVED'
        """,
        node_rid,
    )
    return {e["target_node"] for e in edges if scope_admits(e["rid_types"])}


@dataclass
class RecipientPlan:
    authorized: List[str] = field(default_factory=list)
    # target_node -> reason
    unauthorized: Dict[str, str] = field(default_factory=dict)
    # koi_net_events.event_id values that carried this fact out (any type)
    original_event_ids: List[str] = field(default_factory=list)


async def select_recipients(
    conn: asyncpg.Connection,
    node_rid: str,
    *,
    fact_id: str | UUID,
    episode_id: Optional[str | UUID],
) -> RecipientPlan:
    """Who gets the tombstone, and who must not.

    authorized: every peer holding an APPROVED outbound POLL edge from this
      node whose scope admits `knowledge_fact` — computed with `scope_admits`,
      the mirror of the poll filter. This is the access policy as it stands
      NOW; a peer whose edge was narrowed or revoked since the original
      delivery is not authorized, whatever it holds.

    unauthorized: peers with POSITIVE evidence of having received the original
      fact — their node appears in `confirmed_by` on a knowledge_episode /
      knowledge_fact event that carried this fact id — but no admitting edge
      now. Recorded so the operator can see "this peer may hold the fact live
      and policy forbids telling it". `delivered_to` is deliberately NOT used
      as evidence: it also marks scope-excluded events.
    """
    plan = RecipientPlan()
    admitting = await _admitting_peers(conn, node_rid)
    plan.authorized = sorted(admitting)

    # Original-recipient evidence. Both event shapes that can carry a fact:
    # the bundled episode (facts[] contains {"id": ...}) and a standalone fact.
    fid = str(fact_id)
    rids = [fact_rid(fid)]
    if episode_id:
        rids.append(episode_rid(episode_id))
    rows = await conn.fetch(
        """
        SELECT event_id::TEXT AS event_id, confirmed_by
        FROM koi_net_events
        WHERE rid = ANY($1::text[])
          AND event_id IS NOT NULL
          AND (
                (contents->>'_koi_domain' = 'knowledge_episode'
                 AND contents->'payload'->'facts' @> $2::jsonb)
             OR (contents->>'_koi_domain' = 'knowledge_fact'
                 AND contents->'payload'->>'id' = $3)
          )
        ORDER BY queued_at
        """,
        rids, json.dumps([{"id": fid}]), fid,
    )
    confirmed: set[str] = set()
    for r in rows:
        plan.original_event_ids.append(r["event_id"])
        for n in (r["confirmed_by"] or []):
            confirmed.add(n)
    for node in sorted(confirmed):
        if node == node_rid:
            continue
        if node not in admitting:
            plan.unauthorized[node] = "confirmed_original_recipient_edge_no_longer_admits_knowledge_fact"
    return plan


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def wire_identity(retracted_by: Optional[str]) -> Optional[str]:
    """What `retracted_by` becomes on the wire.

    A session-token caller's identity is the operator's EMAIL
    (api/auth_deps.py); before this branch it was only ever logged locally.
    It must not travel to peers, be stored in their ledgers, or be re-exposed
    by their lookups (review finding 4). Service identities (`service:…`) are
    opaque role names and pass through; anything else becomes "operator". The
    real identity stays in the publisher's own ledger row.
    """
    if not retracted_by:
        return None
    if retracted_by.startswith("service:"):
        return retracted_by
    return "operator"


def build_retraction_payload(
    snapshot: Dict[str, Any],
    *,
    retraction_id: int,
    origin_node: str,
    valid_to_iso: str,
    reason: Optional[str],
    retracted_by: Optional[str],
    attempt: int,
    source_document: Optional[str],
    original_event_ids: Sequence[str],
    event_id: str,
) -> Dict[str, Any]:
    if snapshot.get("valid_to") != valid_to_iso:
        raise RetractionError(
            f"snapshot valid_to {snapshot.get('valid_to')!r} != committed {valid_to_iso!r}")
    payload = dict(snapshot)
    payload["retraction"] = {
        "retraction_id": retraction_id,
        "origin_node": origin_node,
        "valid_to": valid_to_iso,
        "reason": reason,
        "retracted_by": wire_identity(retracted_by),
        "attempt": attempt,
        "episode_rid": episode_rid(snapshot["episode_id"]) if snapshot.get("episode_id") else None,
        "source_document": source_document,
        "original_event_ids": list(original_event_ids),
    }
    payload["_federation_event_id"] = event_id
    return payload


def is_retraction_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("retraction"), dict)


# ---------------------------------------------------------------------------
# Publisher: the retraction transaction
# ---------------------------------------------------------------------------

@dataclass
class RetractionResult:
    fact_id: str
    retracted: bool
    already_retracted: bool
    valid_to: Optional[str]
    subject_uri: Optional[str]
    predicate: Optional[str]
    object_uri: Optional[str]
    episode_id: Optional[str]
    retraction_id: Optional[int]
    federation_enabled: bool
    node_rid: Optional[str]
    deliveries: List[Dict[str, Any]] = field(default_factory=list)
    ledgered: bool = True   # False only for on_missing_ledger="skip" with 127 absent
    no_admitting_peers: bool = False  # federation on, queue present, no edge admits knowledge_fact


async def retract_fact_transactional(
    conn: asyncpg.Connection,
    *,
    fact_id: UUID,
    event_queue: Any,
    federation_enabled: bool,
    reason: Optional[str],
    retracted_by: Optional[str],
    on_missing_ledger: str = "raise",
) -> Optional[RetractionResult]:
    """Retract `fact_id` and record every outbound obligation, on `conn`.

    The CALLER holds the transaction (`async with conn.transaction():`). This
    function never commits and never acquires another connection; every write
    — knowledge_facts, knowledge_fact_retractions, koi_net_events,
    knowledge_fact_retraction_deliveries — lands on `conn` so they commit
    together or not at all. That is the atomicity #67 asks for, and it is
    real rather than claimed only because `EventQueue.add` takes `conn=`.

    Returns None if the fact does not exist. Returns a result with
    `already_retracted=True` (and no new obligations) if valid_to was already
    set — the endpoint's idempotent no-op. Raises LedgerUnavailable BEFORE any
    write if migration 127 is not applied: a retraction whose obligation
    cannot be recorded is the silent-loss class this module exists to close.

    event_queue: the node's EventQueue (its `.node_rid` is this node's
      identity) or None when federation is not configured.
    federation_enabled: the KOI_FEDERATE_KNOWLEDGE gate, read by the caller.
    on_missing_ledger: "raise" (the endpoint: refuse before any write) or
      "skip" (create_episode's supersession auto-retire: the valid_to write
      goes ahead, the obligation is NOT recorded, a warning is logged once per
      process and the result carries `ledgered=False`). "skip" exists because
      refusing would fail every ingest on a publisher that has not run 127,
      which is worse than the audit finding the gap later.
    """
    global _warned_ledger_absent_publisher
    ledgered = await ledger_available(conn)
    if not ledgered:
        if on_missing_ledger != "skip":
            await require_ledger(conn)
        if not _warned_ledger_absent_publisher:
            _warned_ledger_absent_publisher = True
            logger.warning(
                "fact_retraction.unledgered_retraction: migration 127 is not applied; "
                "valid_to is written but NO federation obligation is recorded "
                "(logged once per process; the audit will report these as possibly_live)")

    row = await conn.fetchrow(
        """
        SELECT f.id, f.episode_id, f.subject_uri, f.predicate, f.object_uri,
               f.object_literal, f.fact_text, f.valid_from, f.valid_to, f.created_at,
               f.group_id, f.source_node_rid, f.turn_range_start, f.turn_range_end,
               e.source_document
        FROM knowledge_facts f
        LEFT JOIN knowledge_episodes e ON e.id = f.episode_id
        WHERE f.id = $1
        FOR UPDATE OF f
        """,
        fact_id,
    )
    if row is None:
        return None

    node_rid = getattr(event_queue, "node_rid", None) if event_queue is not None else None
    origin = node_rid or LOCAL_ORIGIN

    if row["valid_to"] is not None:
        return RetractionResult(
            fact_id=str(fact_id), retracted=False, already_retracted=True,
            valid_to=_iso(row["valid_to"]), subject_uri=row["subject_uri"],
            predicate=row["predicate"], object_uri=row["object_uri"],
            episode_id=_iso(row["episode_id"]), retraction_id=None,
            federation_enabled=bool(federation_enabled), node_rid=node_rid,
        )

    updated = await conn.fetchrow(
        """
        UPDATE knowledge_facts
        SET valid_to = NOW()
        WHERE id = $1 AND valid_to IS NULL
        RETURNING id, episode_id, subject_uri, predicate, object_uri, object_literal,
                  fact_text, valid_from, valid_to, created_at, group_id, source_node_rid,
                  turn_range_start, turn_range_end
        """,
        fact_id,
    )
    if updated is None:
        # FOR UPDATE above makes this unreachable in practice; keep the
        # idempotent answer rather than an assertion.
        refetched = await conn.fetchval(
            "SELECT valid_to FROM knowledge_facts WHERE id = $1", fact_id)
        return RetractionResult(
            fact_id=str(fact_id), retracted=False, already_retracted=True,
            valid_to=_iso(refetched), subject_uri=row["subject_uri"],
            predicate=row["predicate"], object_uri=row["object_uri"],
            episode_id=_iso(row["episode_id"]), retraction_id=None,
            federation_enabled=bool(federation_enabled), node_rid=node_rid,
        )

    snapshot = snapshot_from_row(updated)
    valid_to_iso = snapshot["valid_to"]
    assert valid_to_iso == updated["valid_to"].isoformat()

    if not ledgered:
        return RetractionResult(
            fact_id=str(fact_id), retracted=True, already_retracted=False,
            valid_to=valid_to_iso, subject_uri=updated["subject_uri"],
            predicate=updated["predicate"], object_uri=updated["object_uri"],
            episode_id=_iso(updated["episode_id"]), retraction_id=None,
            federation_enabled=bool(federation_enabled), node_rid=node_rid,
            ledgered=False,
        )

    retraction_id = await conn.fetchval(
        """
        INSERT INTO knowledge_fact_retractions
            (fact_id, valid_to, origin_node, episode_id, reason, retracted_by,
             source_document, source_node_rid, fact_snapshot, applied_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, NOW())
        RETURNING id
        """,
        fact_id, updated["valid_to"], origin, updated["episode_id"], reason, retracted_by,
        row["source_document"], updated["source_node_rid"], json.dumps(snapshot),
    )

    result = RetractionResult(
        fact_id=str(fact_id), retracted=True, already_retracted=False,
        valid_to=valid_to_iso, subject_uri=updated["subject_uri"],
        predicate=updated["predicate"], object_uri=updated["object_uri"],
        episode_id=_iso(updated["episode_id"]), retraction_id=retraction_id,
        federation_enabled=bool(federation_enabled), node_rid=node_rid,
    )

    if not federation_enabled or event_queue is None or not node_rid:
        # Local-only retraction. The ledger row above still exists so the
        # audit can find a retraction that never federated.
        return result

    plan = await select_recipients(
        conn, node_rid, fact_id=fact_id, episode_id=updated["episode_id"])
    if not plan.authorized:
        # Federation is on and a queue exists, yet no APPROVED outbound edge
        # admits knowledge_fact. Legitimate on a node with no knowledge peers;
        # ALSO exactly what a scope-vocabulary change would look like (the
        # unmerged 119_edge_scope_contexts rewrites rid_types as ORN contexts,
        # under which `scope_admits` admits nobody — review finding 19).
        # test_scope_admits_agrees_with_a_real_poll pins the mirror; this log
        # line is the runtime tell. Not an error: the retraction is valid and
        # the ledger row is written; the audit reports the peers.
        n_edges = await conn.fetchval(
            "SELECT count(*) FROM koi_net_edges WHERE source_node = $1 AND status = 'APPROVED'",
            node_rid)
        logger.warning(
            "fact_retraction.no_admitting_peers fact=%s approved_outbound_edges=%s "
            "(no edge scope admits knowledge_fact; retraction is local-only)",
            fact_id, n_edges)
        result.no_admitting_peers = True

    for target in plan.authorized:
        event_id = str(uuid4())
        payload = build_retraction_payload(
            snapshot,
            retraction_id=retraction_id, origin_node=node_rid, valid_to_iso=valid_to_iso,
            reason=reason, retracted_by=retracted_by, attempt=1,
            source_document=row["source_document"],
            original_event_ids=plan.original_event_ids, event_id=event_id,
        )
        queued_id = await event_queue.add(
            event_type=EVENT_TYPE,
            rid=fact_rid(fact_id),
            contents={"_koi_domain": DOMAIN, "payload": payload},
            target_node=target,
            event_id=event_id,
            ttl_hours=RETRACTION_TTL_HOURS,
            conn=conn,
        )
        if queued_id is None:
            raise RetractionError(
                f"event {event_id} for {target} was reported as a duplicate; "
                f"a fresh uuid4 cannot collide — refusing to record a phantom delivery")
        await conn.execute(
            """
            INSERT INTO knowledge_fact_retraction_deliveries
                (retraction_id, target_node, event_id, attempt, attempt_history,
                 state, state_reason, queued_at)
            VALUES ($1, $2, $3::uuid, 1, $4::jsonb, $5, 'edge_admits_knowledge_fact', NOW())
            """,
            retraction_id, target, event_id,
            json.dumps([{"attempt": 1, "event_id": event_id}]), STATE_QUEUED,
        )
        result.deliveries.append({
            "target_node": target, "state": STATE_QUEUED, "event_id": event_id, "attempt": 1,
        })

    for target, why in plan.unauthorized.items():
        await conn.execute(
            """
            INSERT INTO knowledge_fact_retraction_deliveries
                (retraction_id, target_node, event_id, attempt, state, state_reason)
            VALUES ($1, $2, NULL, 0, $3, $4)
            """,
            retraction_id, target, STATE_UNAUTHORIZED, why,
        )
        result.deliveries.append({
            "target_node": target, "state": STATE_UNAUTHORIZED, "event_id": None,
            "attempt": 0, "reason": why,
        })

    logger.info(
        "fact_retraction.retracted fact=%s valid_to=%s retraction_id=%s "
        "authorized=%d unauthorized=%d by=%s reason=%r",
        fact_id, valid_to_iso, retraction_id, len(plan.authorized),
        len(plan.unauthorized), retracted_by, reason,
    )
    return result


# ---------------------------------------------------------------------------
# Publisher: transport observations
# ---------------------------------------------------------------------------

def _valid_uuids(ids: Iterable[Any]) -> List[str]:
    """Only well-formed UUIDs reach a `::uuid[]` bind. One junk id in a confirm
    batch used to raise DataError and lose the WHOLE batch's receipts and
    reports while the transport had already recorded them (review finding 11)."""
    out: List[str] = []
    for raw in ids or []:
        try:
            out.append(str(UUID(str(raw))))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


async def record_deliveries(
    conn: asyncpg.Connection,
    requesting_node: str,
    handed_over_ids: Sequence[str],
    excluded_ids: Sequence[str],
) -> Dict[str, int]:
    """poll() observer: handed-over → delivered; scope-excluded → failed.

    A retraction event is unicast to a peer whose edge admitted the domain at
    queue time. If poll() later EXCLUDES it, the edge changed in between —
    the obligation is now unfulfillable under policy and is closed as
    `failed` (named; the sweep reopens it if the edge admits again).

    poll() runs without an explicit transaction, so its `delivered_to` write
    autocommits before this observer runs. If the observer fails the ledger
    stays `queued` while the event was handed over (review finding P7). That
    is self-healing, not silent: the peer's confirm moves the row to
    `received`/`applied` from `queued` too, and if no confirm ever comes the
    sweep re-queues it — a duplicate delivery the recipient treats as
    `already_tombstoned`.
    """
    if not await ledger_available(conn):
        return {"delivered": 0, "failed": 0}
    delivered = failed = 0
    handed_over_ids = _valid_uuids(handed_over_ids)
    excluded_ids = _valid_uuids(excluded_ids)
    if handed_over_ids:
        status = await conn.execute(
            f"""
            UPDATE knowledge_fact_retraction_deliveries
            SET state = '{STATE_DELIVERED}', delivered_at = NOW(),
                state_changed_at = NOW(), state_reason = 'handed_over_at_poll'
            WHERE target_node = $1
              AND event_id = ANY($2::uuid[])
              AND state IN ('{STATE_QUEUED}', '{STATE_RETRYING}')
            """,
            requesting_node, list(handed_over_ids),
        )
        delivered = int(status.split()[-1])
    if excluded_ids:
        status = await conn.execute(
            f"""
            UPDATE knowledge_fact_retraction_deliveries
            SET state = '{STATE_FAILED}', state_changed_at = NOW(),
                state_reason = 'edge_scope_excluded_at_poll'
            WHERE target_node = $1
              AND event_id = ANY($2::uuid[])
              AND state IN ('{STATE_QUEUED}', '{STATE_RETRYING}', '{STATE_DELIVERED}')
            """,
            requesting_node, list(excluded_ids),
        )
        failed = int(status.split()[-1])
    return {"delivered": delivered, "failed": failed}


def make_delivery_observer() -> Callable[..., Awaitable[None]]:
    """The hook installed on `EventQueue.delivery_observer` at startup.

    Only `EventQueue.poll` calls it. The WEBHOOK push path
    (`peek_undelivered` → `mark_delivered`) does not, so a webhook peer's
    deliveries go `queued` → `received` without a `delivered` step (review
    finding 18). Zero WEBHOOK edges exist on either node; wire the observer
    into `_push_webhook_peers` before approving one.
    """

    async def _observer(conn, requesting_node, handed_over_ids, excluded_ids):
        await record_deliveries(conn, requesting_node, handed_over_ids, excluded_ids)

    return _observer


async def record_receipts(
    conn: asyncpg.Connection,
    confirming_node: str,
    event_ids: Sequence[str],
) -> int:
    """confirm(): receipt only. Never touches applied/rejected rows."""
    event_ids = _valid_uuids(event_ids)
    if not event_ids or not await ledger_available(conn):
        return 0
    status = await conn.execute(
        f"""
        UPDATE knowledge_fact_retraction_deliveries
        SET state = '{STATE_RECEIVED}', received_at = COALESCE(received_at, NOW()),
            state_changed_at = NOW(), state_reason = 'peer_confirmed_receipt'
        WHERE target_node = $1
          AND event_id = ANY($2::uuid[])
          AND state IN ('{STATE_QUEUED}', '{STATE_DELIVERED}', '{STATE_RETRYING}')
        """,
        confirming_node, list(event_ids),
    )
    return int(status.split()[-1])


async def count_open_deliveries(
    conn: asyncpg.Connection,
    target_node: str,
    event_ids: Sequence[str],
) -> int:
    """How many of `event_ids` are this peer's not-yet-received retraction
    deliveries. Used to make an UNSIGNED confirm's withheld receipts visible
    in the log without recording them."""
    event_ids = _valid_uuids(event_ids)
    if not event_ids or not await ledger_available(conn):
        return 0
    return int(await conn.fetchval(
        f"""
        SELECT count(*) FROM knowledge_fact_retraction_deliveries
        WHERE target_node = $1
          AND event_id = ANY($2::uuid[])
          AND state IN ('{STATE_QUEUED}', '{STATE_DELIVERED}', '{STATE_RETRYING}')
        """,
        target_node, list(event_ids),
    ) or 0)


def _report_to_state(report: Dict[str, Any]) -> tuple[str, str]:
    status = report.get("status")
    if status == REPORT_REJECTED:
        return STATE_REJECTED, str(report.get("reason") or "peer_rejected")
    if status == REPORT_APPLIED:
        return STATE_APPLIED, "peer_reported_applied"
    if status == REPORT_ALREADY_TOMBSTONED:
        if report.get("valid_to_matches") is False:
            # AC2 says the SAME valid_to. A peer holding a different tombstone
            # has not met it; the fact is not active there, but recording this
            # as `applied` would hide the violation (review finding P1).
            return STATE_REJECTED, "peer_holds_different_valid_to"
        return STATE_APPLIED, "peer_already_tombstoned"
    if status == REPORT_PENDING:
        # The fact is ABSENT on the peer; it recorded a pending tombstone.
        # Never `applied` (review finding 2): nothing is stored there yet.
        return STATE_PENDING, "peer_recorded_pending_tombstone_fact_absent"
    return STATE_REJECTED, f"unrecognized_report_status:{status!r}"


async def record_applications(
    conn: asyncpg.Connection,
    confirming_node: str,
    applications: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply the recipient's application reports to the deliveries ledger.

    Each report must name `event_id`. The row must belong to `confirming_node`
    — a node cannot acknowledge on another node's behalf; such reports are
    counted as `ignored`. Rows already terminal (`applied`, `rejected`,
    `failed`, `unauthorized`) keep their first verdict: a later report is
    counted as `already_terminal` and logged at WARNING when it differs, but
    NOT stored (review finding P6 — the earlier docstring claimed a history
    that did not exist). `pending` and `unverifiable` are NOT terminal: a
    later genuine report advances them. A reopenable rejection is re-queued
    by the sweep under a fresh event id, and the report on THAT event lands
    normally. The report itself is stored VERBATIM in `application`.
    Every rejection is logged at WARNING (`fact_retraction.delivery_rejected`)
    so terminal outcomes are visible in the aggregate, not only per UUID.

    Caller responsibility: `confirming_node` must be AUTHENTICATED (the
    signed envelope's source_node). The confirm endpoint refuses to pass
    applications from an unsigned request (review finding P4).
    """
    summary = {"applied": 0, "pending": 0, "rejected": 0, "ignored": 0, "unknown_event": 0,
               "already_terminal": 0}
    if not await ledger_available(conn):
        summary["ignored"] = sum(1 for _ in applications)
        return summary
    for report in applications:
        if not isinstance(report, dict) or not report.get("event_id"):
            summary["ignored"] += 1
            continue
        try:
            eid = str(UUID(str(report["event_id"])))
        except (ValueError, TypeError):
            summary["ignored"] += 1
            continue
        row = await conn.fetchrow(
            """
            SELECT d.id, d.target_node, d.state, r.fact_id::TEXT AS fact_id
            FROM knowledge_fact_retraction_deliveries d
            JOIN knowledge_fact_retractions r ON r.id = d.retraction_id
            WHERE d.event_id = $1::uuid
            """,
            eid,
        )
        if row is None:
            summary["unknown_event"] += 1
            continue
        if row["target_node"] != confirming_node:
            logger.warning(
                "fact_retraction.application_from_wrong_node event=%s row_target=%s reporter=%s",
                eid, row["target_node"], confirming_node)
            summary["ignored"] += 1
            continue
        state, why = _report_to_state(report)
        if row["state"] in TERMINAL_STATES:
            summary["already_terminal"] += 1
            if state != row["state"]:
                logger.warning(
                    "fact_retraction.application_after_terminal event=%s node=%s row_state=%s "
                    "later_report=%s (kept first verdict)",
                    eid, confirming_node, row["state"], json.dumps(report, default=_jsonable)[:500])
            continue
        await conn.execute(
            """
            UPDATE knowledge_fact_retraction_deliveries
            SET state = $2, state_reason = $3, application = $4::jsonb,
                applied_at = CASE WHEN $2 = 'applied' THEN NOW() ELSE applied_at END,
                received_at = COALESCE(received_at, NOW()),
                state_changed_at = NOW()
            WHERE id = $1
            """,
            row["id"], state, why, json.dumps(report, default=_jsonable),
        )
        summary[state] += 1
        if state == STATE_REJECTED:
            logger.warning(
                "fact_retraction.delivery_rejected fact=%s peer=%s event=%s reason=%s",
                row["fact_id"], confirming_node, eid, why)
    return summary


# ---------------------------------------------------------------------------
# Publisher: retry / expiry sweep
# ---------------------------------------------------------------------------

async def plan_requeue(
    conn: asyncpg.Connection,
    *,
    node_rid: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[Dict[str, Any]]:
    """Pure read. What the sweep WOULD do to every open delivery.

    Rules, in order:
      * received (the peer confirmed): event expired, no report → unverifiable,
        else wait. Receipt is evidence the edge cannot un-make: a later scope
        change never turns a `received` row into `failed` (review finding 3),
        and the peer's application report still lands on it.
      * edge no longer admits knowledge_fact       → fail  (policy changed)
      * rejected with a `ledger_unavailable…` reason → retry now (fresh event;
        the peer consumed the old one) while attempts remain, else fail
      * failed with a scope reason, edge admits again → retry now, else fail
      * event row gone, or expired, and attempts left → retry (fresh event)
      * event row gone, or expired, attempts exhausted → fail
      * otherwise                                    → wait
    `pending` rows are not selected: the peer holds a pending tombstone and no
    fact; there is nothing to retry and nothing to fail.
    """
    await require_ledger(conn)
    # Same rule as select_recipients: a peer is admitted if ANY of its APPROVED
    # outbound edges admits the domain. (events_poll reads ONE edge row with
    # fetchrow; with one POLL edge per pair — the live shape — the two agree.)
    admitting = await _admitting_peers(conn, node_rid)
    rows = await conn.fetch(
        """
        SELECT d.id, d.retraction_id, d.target_node, d.event_id::TEXT AS event_id,
               d.attempt, d.state, d.state_reason, r.fact_id::TEXT AS fact_id, r.valid_to,
               ev.expires_at, ev.confirmed_by
        FROM knowledge_fact_retraction_deliveries d
        JOIN knowledge_fact_retractions r ON r.id = d.retraction_id
        LEFT JOIN koi_net_events ev ON ev.event_id = d.event_id
        WHERE (d.state IN ('queued', 'delivered', 'retrying', 'received')
               OR (d.state = 'rejected' AND d.state_reason LIKE $2)
               OR (d.state = 'failed' AND d.state_reason = ANY($3::text[])))
          AND r.origin_node = $1
        ORDER BY d.id
        """,
        node_rid, REOPENABLE_REJECT_PREFIX + "%", sorted(SCOPE_FAIL_REASONS),
    )
    now = await conn.fetchval("SELECT NOW()")
    plan: List[Dict[str, Any]] = []
    for r in rows:
        expired = r["expires_at"] is None or r["expires_at"] <= now
        admits = r["target_node"] in admitting
        item = {
            "delivery_id": r["id"], "retraction_id": r["retraction_id"],
            "fact_id": r["fact_id"], "target_node": r["target_node"],
            "event_id": r["event_id"], "attempt": r["attempt"], "state": r["state"],
            "event_expired": expired,
        }
        if r["state"] == STATE_RECEIVED:
            # Receipt is kept whatever the edge does afterwards.
            if expired:
                item.update(action="unverifiable",
                            reason="receipt_confirmed_no_application_report_before_expiry")
            else:
                item.update(action="wait", reason="receipt_confirmed_report_may_still_arrive")
        elif r["state"] == STATE_FAILED:
            # Only scope failures are selected. Reopen iff the edge admits again.
            if not admits:
                continue  # still narrow: nothing to do, stays failed
            if r["attempt"] < max_attempts:
                item.update(action="retry", reason="edge_admits_again_after_scope_failure",
                            next_attempt=r["attempt"] + 1)
            else:
                item.update(action="fail", reason=f"attempts_exhausted:{max_attempts}")
        elif not admits:
            if r["state"] == STATE_REJECTED:
                continue  # unmigrated peer AND no longer admitted: leave the rejection
            item.update(action="fail", reason="edge_no_longer_admits_knowledge_fact")
        elif r["state"] == STATE_REJECTED:
            # Reopenable: the peer lacked migration 127. Do not wait for expiry —
            # the peer already consumed and confirmed the old event.
            if r["attempt"] < max_attempts:
                item.update(action="retry", reason="peer_unmigrated_retry",
                            next_attempt=r["attempt"] + 1)
            else:
                item.update(action="fail", reason=f"attempts_exhausted:{max_attempts}")
        elif expired:
            if r["attempt"] < max_attempts:
                item.update(action="retry", reason="event_expired_unconfirmed",
                            next_attempt=r["attempt"] + 1)
            else:
                item.update(action="fail", reason=f"attempts_exhausted:{max_attempts}")
        else:
            item.update(action="wait", reason="event_live_awaiting_poll")
        plan.append(item)
    return plan


async def apply_requeue(
    conn: asyncpg.Connection,
    plan: Sequence[Dict[str, Any]],
    *,
    event_queue: Any,
    node_rid: str,
) -> Dict[str, int]:
    """Execute a plan from `plan_requeue` on `conn` (caller holds the transaction)."""
    await require_ledger(conn)
    summary = {"retried": 0, "failed": 0, "unverifiable": 0, "waited": 0}
    for item in plan:
        action = item.get("action")
        if action == "wait":
            summary["waited"] += 1
            continue
        if action in ("fail", "unverifiable"):
            state = STATE_FAILED if action == "fail" else STATE_UNVERIFIABLE
            await conn.execute(
                """
                UPDATE knowledge_fact_retraction_deliveries
                SET state = $2, state_reason = $3, state_changed_at = NOW()
                WHERE id = $1
                """,
                item["delivery_id"], state, item.get("reason"),
            )
            summary["failed" if action == "fail" else "unverifiable"] += 1
            # Visible in the aggregate (review finding 8): a terminal failure
            # used to be findable only by reading this row's UUID.
            log = logger.warning if action == "fail" else logger.info
            log("fact_retraction.delivery_%s delivery=%s fact=%s peer=%s attempt=%s reason=%s",
                "failed" if action == "fail" else "unverifiable",
                item["delivery_id"], item.get("fact_id"), item.get("target_node"),
                item.get("attempt"), item.get("reason"))
            continue
        if action != "retry":
            continue
        ret = await conn.fetchrow(
            """
            SELECT r.id, r.fact_id, r.valid_to, r.reason, r.retracted_by, r.source_document,
                   r.fact_snapshot, d.attempt, d.attempt_history
            FROM knowledge_fact_retractions r
            JOIN knowledge_fact_retraction_deliveries d ON d.retraction_id = r.id
            WHERE d.id = $1
            FOR UPDATE OF d
            """,
            item["delivery_id"],
        )
        snapshot = ret["fact_snapshot"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        history = ret["attempt_history"]
        if isinstance(history, str):
            history = json.loads(history)
        history = list(history or [])
        attempt = ret["attempt"] + 1
        event_id = str(uuid4())
        payload = build_retraction_payload(
            snapshot,
            retraction_id=ret["id"], origin_node=node_rid,
            valid_to_iso=ret["valid_to"].isoformat(), reason=ret["reason"],
            retracted_by=ret["retracted_by"], attempt=attempt,
            source_document=ret["source_document"],
            # The earlier attempts' event ids, so the recipient can correlate
            # a retry with what it may already have seen (review finding 16).
            original_event_ids=[h["event_id"] for h in history if h.get("event_id")],
            event_id=event_id,
        )
        queued = await event_queue.add(
            event_type=EVENT_TYPE, rid=fact_rid(ret["fact_id"]),
            contents={"_koi_domain": DOMAIN, "payload": payload},
            target_node=item["target_node"], event_id=event_id,
            ttl_hours=RETRACTION_TTL_HOURS, conn=conn,
        )
        if queued is None:
            raise RetractionError(f"requeue event {event_id} reported duplicate")
        history = history + [{"attempt": attempt, "event_id": event_id}]
        await conn.execute(
            """
            UPDATE knowledge_fact_retraction_deliveries
            SET event_id = $2::uuid, attempt = $3, attempt_history = $4::jsonb,
                state = 'retrying', state_reason = $5, queued_at = NOW(),
                state_changed_at = NOW()
            WHERE id = $1
            """,
            item["delivery_id"], event_id, attempt, json.dumps(history), item.get("reason"),
        )
        summary["retried"] += 1
        logger.info("fact_retraction.delivery_retrying delivery=%s fact=%s peer=%s attempt=%s reason=%s",
                    item["delivery_id"], item.get("fact_id"), item["target_node"], attempt,
                    item.get("reason"))
    return summary


async def sweep_once(
    pool: Any,
    event_queue: Any,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Dict[str, int]:
    """plan + apply in one transaction. For the poller loop / CLI."""
    node_rid = getattr(event_queue, "node_rid", None)
    if not node_rid:
        return {"retried": 0, "failed": 0, "unverifiable": 0, "waited": 0}
    async with pool.acquire() as conn:
        if not await ledger_available(conn):
            return {"retried": 0, "failed": 0, "unverifiable": 0, "waited": 0}
        async with conn.transaction():
            plan = await plan_requeue(conn, node_rid=node_rid, max_attempts=max_attempts)
            summary = await apply_requeue(conn, plan, event_queue=event_queue, node_rid=node_rid)
    if summary["retried"] or summary["failed"] or summary["unverifiable"]:
        logger.info("fact_retraction.sweep node=%s retried=%d failed=%d unverifiable=%d waited=%d",
                    node_rid, summary["retried"], summary["failed"], summary["unverifiable"],
                    summary["waited"])
    return summary


# ---------------------------------------------------------------------------
# Recipient
# ---------------------------------------------------------------------------

async def record_inbound_tombstone(
    conn: asyncpg.Connection,
    *,
    fact_id: str | UUID,
    valid_to: datetime,
    origin_node: str,
    episode_id: Optional[str | UUID],
    snapshot: Dict[str, Any],
    applied: bool,
    reason: Optional[str] = None,
    retracted_by: Optional[str] = None,
    source_document: Optional[str] = None,
) -> int:
    """Upsert the ledger row for a tombstone that arrived over federation.

    `applied=False` records a PENDING tombstone (fact absent locally).
    Idempotent on (fact_id, valid_to, origin_node); a re-delivery that finds
    the row already applied keeps applied_at.
    """
    await require_ledger(conn)
    fid = fact_id if isinstance(fact_id, UUID) else UUID(str(fact_id))
    eid = None
    if episode_id:
        eid = episode_id if isinstance(episode_id, UUID) else UUID(str(episode_id))
    return await conn.fetchval(
        """
        INSERT INTO knowledge_fact_retractions
            (fact_id, valid_to, origin_node, episode_id, reason, retracted_by,
             source_document, source_node_rid, fact_snapshot, applied_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb,
                CASE WHEN $10 THEN NOW() ELSE NULL END)
        ON CONFLICT (fact_id, valid_to, origin_node) DO UPDATE
            SET applied_at = COALESCE(knowledge_fact_retractions.applied_at,
                                      CASE WHEN $10 THEN NOW() ELSE NULL END)
        RETURNING id
        """,
        fid, valid_to, origin_node, eid, reason, retracted_by, source_document,
        snapshot.get("source_node_rid"), json.dumps(snapshot, default=_jsonable), applied,
    )


async def apply_pending_tombstones(
    conn: asyncpg.Connection,
    fact_ids: Sequence[str | UUID],
) -> List[str]:
    """After facts are inserted: land any pending tombstone for them.

    Returns the fact ids that were tombstoned by this call. Safe when the
    ledger is absent (returns []; warns once per process, then debug) so an un-migrated recipient
    keeps applying episodes — the guard is deliberate: crashing every episode
    apply on a peer that has not run 127 would be the worse failure.

    Several pending rows for one fact (two origins, two values): the EARLIEST
    valid_to lands and every pending row for that fact is marked applied —
    "applied" on a ledger row means "the local fact carries a tombstone", not
    "this row's exact value is the one that landed" (review finding P5).
    """
    global _warned_ledger_absent
    ids = [str(x) for x in fact_ids if x]
    if not ids:
        return []
    if not await ledger_available(conn):
        # Warn ONCE per process, then debug: an un-migrated recipient applies
        # many episodes and one WARNING per apply would drown the log.
        if not _warned_ledger_absent:
            _warned_ledger_absent = True
            logger.warning(
                "fact_retraction.pending_skip reason=ledger_unavailable n=%d "
                "(migration 127 not applied here; pending tombstones cannot be honoured; "
                "logged once per process)", len(ids))
        else:
            logger.debug("fact_retraction.pending_skip reason=ledger_unavailable n=%d", len(ids))
        return []
    rows = await conn.fetch(
        """
        WITH pend AS (
            SELECT DISTINCT ON (fact_id) fact_id, valid_to, id
            FROM knowledge_fact_retractions
            WHERE fact_id = ANY($1::uuid[]) AND applied_at IS NULL
            ORDER BY fact_id, valid_to ASC
        ),
        upd AS (
            -- Over federation valid_to only ever moves EARLIER: a pending
            -- tombstone lands on a live fact AND shortens a fact that arrived
            -- with a later validity end (a fact born with an interval, then
            -- retracted). It never moves later and never becomes NULL.
            UPDATE knowledge_facts f
            SET valid_to = p.valid_to
            FROM pend p
            WHERE f.id = p.fact_id AND (f.valid_to IS NULL OR f.valid_to > p.valid_to)
            RETURNING f.id
        )
        UPDATE knowledge_fact_retractions r
        SET applied_at = NOW()
        FROM pend p
        WHERE r.fact_id = p.fact_id AND r.applied_at IS NULL
          AND (r.fact_id IN (SELECT id FROM upd)
               OR EXISTS (SELECT 1 FROM knowledge_facts f2
                          WHERE f2.id = r.fact_id AND f2.valid_to IS NOT NULL))
        RETURNING r.fact_id::TEXT AS fact_id
        """,
        ids,
    )
    landed = sorted({r["fact_id"] for r in rows})
    if landed:
        logger.info("fact_retraction.pending_applied facts=%s", landed)
    return landed


# ---------------------------------------------------------------------------
# Both: the authorized UUID lookup
# ---------------------------------------------------------------------------

async def tombstone_status(conn: asyncpg.Connection, fact_id: UUID) -> Dict[str, Any]:
    """Everything this node knows about `fact_id`'s validity, tombstone included.

    This is the surface ordinary retrieval does not offer: `valid_to` is
    reported whether or not it is set, alongside the ledger rows and (on the
    publisher) each peer's delivery state.
    """
    fact = await conn.fetchrow(
        """
        SELECT id, episode_id, subject_uri, predicate, object_uri, valid_from, valid_to,
               source_node_rid, group_id
        FROM knowledge_facts WHERE id = $1
        """,
        fact_id,
    )
    out: Dict[str, Any] = {
        "fact_id": str(fact_id),
        "exists": fact is not None,
        "valid_to": _iso(fact["valid_to"]) if fact else None,
        "tombstoned": bool(fact and fact["valid_to"] is not None),
        "pending_tombstone": False,
        "ledger_available": False,
        "retractions": [],
        "deliveries": [],
    }
    if fact:
        out.update({
            "episode_id": _iso(fact["episode_id"]),
            "subject_uri": fact["subject_uri"], "predicate": fact["predicate"],
            "object_uri": fact["object_uri"], "valid_from": _iso(fact["valid_from"]),
            "source_node_rid": fact["source_node_rid"], "group_id": fact["group_id"],
        })
    if not await ledger_available(conn):
        return out
    out["ledger_available"] = True
    rets = await conn.fetch(
        """
        SELECT id, valid_to, origin_node, episode_id, reason,
               source_document, applied_at, created_at
        FROM knowledge_fact_retractions WHERE fact_id = $1 ORDER BY id
        """,
        fact_id,
    )
    out["retractions"] = [
        {
            "retraction_id": r["id"], "valid_to": _iso(r["valid_to"]),
            "origin_node": r["origin_node"], "episode_id": _iso(r["episode_id"]),
            # `retracted_by` (an operator email for session callers) stays in
            # the ledger row; it is not part of any lookup surface.
            "reason": r["reason"],
            "source_document": r["source_document"],
            "applied_at": _iso(r["applied_at"]), "created_at": _iso(r["created_at"]),
        }
        for r in rets
    ]
    out["pending_tombstone"] = any(r["applied_at"] is None for r in rets)
    if rets:
        dels = await conn.fetch(
            """
            SELECT d.retraction_id, d.target_node, d.event_id::TEXT AS event_id, d.attempt,
                   d.state, d.state_reason, d.application, d.queued_at, d.delivered_at,
                   d.received_at, d.applied_at, d.state_changed_at
            FROM knowledge_fact_retraction_deliveries d
            WHERE d.retraction_id = ANY($1::bigint[])
            ORDER BY d.retraction_id, d.target_node
            """,
            [r["id"] for r in rets],
        )
        for d in dels:
            app = d["application"]
            if isinstance(app, str):
                app = json.loads(app)
            out["deliveries"].append({
                "retraction_id": d["retraction_id"], "target_node": d["target_node"],
                "event_id": d["event_id"], "attempt": d["attempt"], "state": d["state"],
                "state_reason": d["state_reason"], "application": app,
                "queued_at": _iso(d["queued_at"]), "delivered_at": _iso(d["delivered_at"]),
                "received_at": _iso(d["received_at"]), "applied_at": _iso(d["applied_at"]),
                "state_changed_at": _iso(d["state_changed_at"]),
            })
    return out
