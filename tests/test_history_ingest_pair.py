"""The ingest-side pair (Stream A step 4): one test per fix, each with its own control.

Spec: ~/.claude/plans/history-preserving-website-sensor-artifacts/q1_fixed_pair.md and
q1_fixtures.md, plus the plan's `superseded_at` CASE (history-preserving-website-sensor.md,
"One column, two intents").

The parameterised tests carry a `fix=True|False` pair. `fix=False` DELETES the fix the
test exists for and asserts the measured defect comes back — a fix whose removal changes
nothing was never the fix. The `_write_document` tests drive the real write path, so they
fail if the shipped function stops reaching the pieces tested in isolation above them.

DB tests apply migration 123 inside the test's own transaction and roll it back, against
the conftest-redirected test database. They refuse to run against live personal_koi.
"""
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

import scripts.ingest_document as ingest_document
from scripts.ingest_document import (
    CLAIM_CUTOVER_SQL,
    JOURNAL_SUCCESSOR_PATCH_SQL,
    HistoryClaimRequired,
    HistoryPolicyMisuse,
    JournalSuccessorConflict,
    StagedJournalMisuse,
    StagedWriteViolation,
    _assert_history_flags,
    _claim_cutover,
    _history_claim_triple,
    _history_source_meta,
    _write_document,
    ingest_document_rag_conn,
    upsert_document_memory,
)

REPO = Path(__file__).resolve().parents[1]
DB_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
MIGRATION = REPO / "migrations" / "123_document_history.sql"

K, I, U = "website-sensor", "siteT", "https://t.test/page"
PRED = "document:" + "1" * 64
SUCC = "document:" + "2" * 64
OTHER_URL = "https://t.test/other"

# psql meta-commands and the file's own transaction control; asyncpg speaks neither,
# and the transaction is the TEST's. Dropping them is asserted, not assumed.
_PSQL_ONLY = re.compile(r"^\s*(\\|BEGIN ISOLATION|COMMIT;|SET LOCAL lock_timeout)")


def _migration_body() -> str:
    kept, dropped = [], []
    for ln in MIGRATION.read_text().splitlines():
        (dropped if _PSQL_ONLY.match(ln) else kept).append(ln)
    assert dropped, "the stripper matched nothing — the migration's psql preamble moved"
    assert any(d.strip() == "COMMIT;" for d in dropped), "COMMIT; was NOT stripped"
    assert len(kept) > len(dropped) * 10, "the stripper ate the migration"
    return "\n".join(kept)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn():
    """Single connection, migration 123 applied, transaction rolled back at teardown."""
    _conn = await asyncpg.connect(DB_URL)
    # Fixtures run on a scratch database (plan decision D-C), never through live data.
    assert await _conn.fetchval("SELECT current_database()") != "personal_koi", (
        "refusing to run history fixtures against live personal_koi")
    tx = _conn.transaction()
    await tx.start()
    await _conn.execute(_migration_body())
    assert await _conn.fetchval("SELECT to_regclass('document_supersession') IS NOT NULL")
    yield _conn
    await tx.rollback()
    await _conn.close()


async def _seed_edge(conn, source_url, predecessor_rid, state="pending", refusal=None):
    return await conn.fetchval(
        """
        INSERT INTO document_supersession
          (chain_id, source_kind, source_id, source_url, predecessor_rid,
           reason, history_policy, state, refusal)
        VALUES (md5($1||'|'||$2||'|'||$3)::uuid, $1, $2, $3, $4, 'changed','keep',$5,$6)
        RETURNING id
        """, K, I, source_url, predecessor_rid, state, refusal)


async def _seed_claimed_predecessor(conn, policy="keep", claim_state="live"):
    retired = "now()" if claim_state == "retired" else "NULL"
    released = "now()" if claim_state == "released" else "NULL"
    await conn.execute(
        f"""INSERT INTO document_source_claim
              (source_kind, source_id, source_url, document_rid, content_hash,
               history_policy, claim_state, retired_at, released_at, last_seen_at)
            VALUES ($1,$2,$3,$4,substr($4,10),$5,$6,{retired},{released},now())""",
        K, I, U, PRED, policy, claim_state)
    await conn.execute(
        """INSERT INTO koi_memories (rid, event_type, source_sensor, content, metadata)
           VALUES ($1,'UPDATE','document-ingest','{"text":"old"}'::jsonb,'{}'::jsonb)""", PRED)


async def _superseded_at(conn, rid):
    return await conn.fetchval("SELECT superseded_at FROM koi_memories WHERE rid = $1", rid)


# =============================================================================
# S2 / plan-edit 20 — the PATCH is keyed on the source triple, not on id alone
# =============================================================================

def _patch_sql(fourth_conjunct: bool) -> str:
    """The negative control is DERIVED from the shipped constant, so it cannot drift."""
    if fourth_conjunct:
        return JOURNAL_SUCCESSOR_PATCH_SQL
    stripped = "\n".join(
        ln for ln in JOURNAL_SUCCESSOR_PATCH_SQL.splitlines()
        if "(source_kind, source_id, source_url)" not in ln)
    assert stripped != JOURNAL_SUCCESSOR_PATCH_SQL, "nothing was removed — the conjunct is absent"
    return stripped


@pytest.mark.anyio
@pytest.mark.parametrize("fix", [True, False], ids=["fix", "NEGATIVE-CONTROL"])
async def test_patch_cannot_bind_another_pages_edge(conn, fix):
    mine = await _seed_edge(conn, U, PRED)
    other = await _seed_edge(conn, OTHER_URL, "document:" + "b" * 64)

    # this ingest belongs to U; it was handed `other`'s journal id
    sql = _patch_sql(fourth_conjunct=fix)
    args = [other, SUCC] + ([K, I, U] if fix else [])
    row = await conn.fetchrow(sql, *args)

    patched = await conn.fetchval(
        "SELECT successor_rid FROM document_supersession WHERE id = $1", other)
    if fix:
        assert row is None, "the wrong page's edge was still matched"
        assert patched is None
    else:
        assert row is not None and patched == SUCC, (
            "the defect did not reproduce: removing the conjunct must let a wrong "
            "--history-journal-id patch another page's edge")

    # POSITIVE CONTROL, both arms: the same statement against the RIGHT row matches.
    ok = await conn.fetchrow(sql, *([mine, SUCC] + ([K, I, U] if fix else [])))
    assert ok is not None and ok["successor_rid"] == SUCC
    # and the replay cases the other conjuncts own still hold under the fix
    assert (await conn.fetchrow(sql, *([mine, SUCC] + ([K, I, U] if fix else [])))) is not None
    dud = "document:" + "d" * 64
    assert (await conn.fetchrow(sql, *([mine, dud] + ([K, I, U] if fix else [])))) is None


# =============================================================================
# S1 / plan-edit 21 — the guard is symmetric
# =============================================================================

def test_history_flag_misuse_is_refused():
    """The guard is symmetric, and refuses every pairing nothing downstream would check.

    The defect each refusal prevents is demonstrated against real statements by
    test_unstaged_journal_run_leaves_a_stale_claim[NEGATIVE-CONTROL].
    """
    with pytest.raises(StagedJournalMisuse, match="without --staged"):
        _assert_history_flags(staged=False, journal_id=7, claim=(K, I, U))
    with pytest.raises(StagedJournalMisuse, match="without --history-journal-id"):
        _assert_history_flags(staged=True, journal_id=None, claim=(K, I, U))
    with pytest.raises(HistoryClaimRequired):                              # S2's precondition
        _assert_history_flags(staged=True, journal_id=7, claim=None)
    with pytest.raises(HistoryPolicyMisuse):                               # never compared
        _assert_history_flags(staged=True, journal_id=7, claim=(K, I, U), policy="keep")
    with pytest.raises(HistoryPolicyMisuse):
        _assert_history_flags(staged=False, journal_id=None, claim=None, policy="keep")

    # POSITIVE CONTROLS: the legitimate shapes are untouched by the guard.
    _assert_history_flags(staged=True, journal_id=7, claim=(K, I, U))      # staged pair
    _assert_history_flags(staged=False, journal_id=None, claim=(K, I, U))  # plain cutover
    _assert_history_flags(staged=False, journal_id=None, claim=(K, I, U), policy="keep")
    _assert_history_flags(staged=False, journal_id=None, claim=None)       # no history at all


@pytest.mark.anyio
@pytest.mark.parametrize("fix", [True, False], ids=["fix", "NEGATIVE-CONTROL"])
async def test_unstaged_journal_run_leaves_a_stale_claim(conn, fix):
    """The consequence the guard exists to prevent, measured on the claim set."""
    jid = await _seed_edge(conn, U, PRED)
    await _seed_claimed_predecessor(conn)

    ran = []
    try:
        if fix:
            _assert_history_flags(staged=False, journal_id=jid, claim=(K, I, U))
        await conn.fetchrow(JOURNAL_SUCCESSOR_PATCH_SQL, jid, SUCC, K, I, U)
        ran.append("patch")
        await conn.fetchrow(CLAIM_CUTOVER_SQL, K, I, U, SUCC, SUCC[9:], "keep")
        ran.append("cutover")
    except StagedJournalMisuse:
        pass

    claim_rid = await conn.fetchval(
        """SELECT document_rid FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U)
    g1_stale_intent = claim_rid != PRED                      # apply step 5, G1
    i18 = await conn.fetchval(                               # live row, no live claim
        """SELECT count(*) FROM koi_memories m
            WHERE m.rid = $1 AND m.superseded_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM document_source_claim c
                               WHERE c.document_rid = m.rid AND c.claim_state='live')""", PRED)
    if fix:
        assert ran == [] and not g1_stale_intent and i18 == 0
    else:
        assert ran == ["patch", "cutover"], "the defect did not reproduce"
        assert g1_stale_intent and i18 == 1, (
            "removing the guard must leave a reveal-unsafe STALE_INTENT and an INV-1 violation")
    # POSITIVE CONTROL for the i18 query: it can see the row at all.
    assert await conn.fetchval(
        "SELECT count(*) FROM koi_memories WHERE rid = $1 AND superseded_at IS NULL", PRED) == 1


# =============================================================================
# S3 / plan-edit 22 — `staged` is read where it is written
# =============================================================================

@pytest.mark.parametrize("fix", [True, False], ids=["fix", "NEGATIVE-CONTROL"])
def test_history_source_meta_is_flat(fix):
    """_history_source_meta writes every history key FLAT, where both readers look.

    The readers themselves are exercised by test_successor_is_born_staged and the
    _write_document tests; this pins the writer's shape.
    """
    meta = _history_source_meta(staged=True, journal_id=7, claim=f"{K}|{I}|{U}", policy=None)
    if not fix:
        meta = {"history": {"staged": True, "journal_id": 7,
                            "source_kind": K, "source_id": I, "source_url": U}}
    if fix:
        assert meta["staged"] is True
        assert _history_claim_triple(meta) == (K, I, U)
        assert meta["history_journal_id"] == 7
    else:
        assert meta.get("staged") is None, "the defect did not reproduce"
        assert _history_claim_triple(meta) is None
        assert meta.get("history_journal_id") is None
        # POSITIVE CONTROL: the value really is in the blob, just unreachable.
        assert meta["history"]["staged"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("fix", [True, False], ids=["fix", "NEGATIVE-CONTROL"])
async def test_successor_is_born_staged(conn, fix):
    meta = ({"slug": "t", "staged": True} if fix
            else {"slug": "t", "history": {"staged": True}})
    await upsert_document_memory(conn, SUCC, "the new bytes", meta)
    staged = await conn.fetchval(
        "SELECT superseded_at IS NOT NULL FROM koi_memories WHERE rid = $1", SUCC)
    if fix:
        assert staged is True
    else:
        assert staged is False, "the defect did not reproduce"
        # what the apply then does — step 7 P2, TERMINAL and reveal-unsafe
        assert not staged, "SUCCESSOR_NOT_STAGED"
    # POSITIVE CONTROL: the probe discriminates — it is not stuck on one answer.
    await conn.execute("UPDATE koi_memories SET superseded_at = now() WHERE rid = $1", SUCC)
    assert await conn.fetchval(
        "SELECT superseded_at IS NOT NULL FROM koi_memories WHERE rid = $1", SUCC) is True


def test_no_history_flags_leave_source_meta_unchanged():
    """Absent every flag, the spread adds nothing: every ingest_path caller (the CLI, the
    documents router, the research-paper wrapper) sees byte-identical source_meta."""
    assert _history_source_meta(staged=False, journal_id=None, claim=None, policy=None) == {}
    # POSITIVE CONTROL: a single legitimate flag does add keys.
    assert _history_source_meta(staged=False, journal_id=None, claim=f"{K}|{I}|{U}",
                                policy=None) == {
        "staged": False, "history_source_kind": K, "history_source_id": I,
        "history_source_url": U}


# =============================================================================
# The superseded_at CASE — one column, two intents (plan step 4a + 4b)
# =============================================================================

@pytest.mark.anyio
async def test_reingest_reveals_a_superseded_row(conn):
    """A→B→A: a fresh, non-staged observation of bytes that were retired revives them."""
    await upsert_document_memory(conn, SUCC, "bytes A", {"slug": "t"})
    await conn.execute("UPDATE koi_memories SET superseded_at = now() WHERE rid = $1", SUCC)
    # POSITIVE CONTROL: the row really was hidden before the re-ingest.
    assert await _superseded_at(conn, SUCC) is not None
    await upsert_document_memory(conn, SUCC, "bytes A", {"slug": "t"})
    assert await _superseded_at(conn, SUCC) is None, "re-ingest did not reveal the row"


@pytest.mark.anyio
async def test_staged_reingest_keeps_a_hidden_rows_stamp(conn):
    """A retried staged ingest is idempotent: it keeps the original stamp, never restamps.

    The stamp is set to a fixed past instant first, because now() is constant inside one
    transaction and could not tell `THEN superseded_at` from `THEN now()`.
    """
    await upsert_document_memory(conn, SUCC, "bytes", {"slug": "t", "staged": True})
    await conn.execute(
        "UPDATE koi_memories SET superseded_at = '2020-01-01T00:00:00Z' WHERE rid = $1", SUCC)
    before = await _superseded_at(conn, SUCC)
    assert before is not None
    await upsert_document_memory(conn, SUCC, "bytes", {"slug": "t", "staged": True})
    assert await _superseded_at(conn, SUCC) == before, "a staged retry restamped or revealed"


@pytest.mark.anyio
async def test_staged_reingest_cannot_hide_a_live_row(conn):
    await upsert_document_memory(conn, SUCC, "bytes", {"slug": "t"})
    assert await _superseded_at(conn, SUCC) is None
    await upsert_document_memory(conn, SUCC, "bytes", {"slug": "t", "staged": True})
    assert await _superseded_at(conn, SUCC) is None, "a staged re-ingest hid a live row"
    # POSITIVE CONTROL: the same staged call on a NEW rid does hide it, so the flag
    # reached the statement.
    await upsert_document_memory(conn, PRED, "other bytes", {"slug": "t", "staged": True})
    assert await _superseded_at(conn, PRED) is not None


# =============================================================================
# The real write path — _write_document end to end
# =============================================================================

def _chunks(text):
    return [{"text": text}], [None]


def _history_meta(**extra):
    meta = {"slug": "t", "group_id": "test-history", "content_hash": SUCC[9:]}
    meta.update(extra)
    return meta


@pytest.mark.anyio
async def test_write_document_staged_run_patches_journal_and_leaves_claim(conn):
    jid = await _seed_edge(conn, U, PRED)
    await _seed_claimed_predecessor(conn)
    chunks, embs = _chunks("the new bytes")
    written = await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
        staged=True, history_journal_id=jid,
        history_source_kind=K, history_source_id=I, history_source_url=U))

    assert written == 1
    assert await conn.fetchval(
        "SELECT successor_rid FROM document_supersession WHERE id = $1", jid) == SUCC
    assert await _superseded_at(conn, SUCC) is not None, "successor was not born hidden"
    assert await conn.fetchval(
        """SELECT document_rid FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U) == PRED, (
        "a staged run advanced the claim; /history/apply is the sole writer")


@pytest.mark.anyio
async def test_write_document_unstaged_run_advances_the_claim(conn):
    await _seed_claimed_predecessor(conn)
    chunks, embs = _chunks("the new bytes")
    await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
        history_source_kind=K, history_source_id=I, history_source_url=U))

    row = await conn.fetchrow(
        """SELECT document_rid, claim_state, history_policy FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U)
    assert (row["document_rid"], row["claim_state"], row["history_policy"]) == (
        SUCC, "live", "keep")
    assert await _superseded_at(conn, SUCC) is None


@pytest.mark.anyio
async def test_write_document_foreign_edge_discards_the_whole_ingest(conn):
    """A JournalSuccessorConflict rolls back bytes, chunks and field membership too."""
    other = await _seed_edge(conn, OTHER_URL, "document:" + "b" * 64)
    chunks, embs = _chunks("the new bytes")
    with pytest.raises(JournalSuccessorConflict):
        await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
            staged=True, history_journal_id=other,
            history_source_kind=K, history_source_id=I, history_source_url=U))

    for table, col in (("koi_memories", "rid"), ("koi_memory_chunks", "document_rid"),
                       ("document_field_membership", "document_rid")):
        n = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {col} = $1", SUCC)
        assert n == 0, f"{table} kept {n} row(s) from a refused ingest"
    assert await conn.fetchval(
        "SELECT successor_rid FROM document_supersession WHERE id = $1", other) is None
    # POSITIVE CONTROL: the connection is still usable, so the savepoint rolled back
    # only this ingest, and the same write with the right edge succeeds.
    mine = await _seed_edge(conn, U, PRED)
    assert await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
        staged=True, history_journal_id=mine,
        history_source_kind=K, history_source_id=I, history_source_url=U)) == 1


@pytest.mark.anyio
async def test_write_document_refuses_journal_id_without_staged_before_writing(conn):
    jid = await _seed_edge(conn, U, PRED)
    chunks, embs = _chunks("the new bytes")
    with pytest.raises(StagedJournalMisuse):
        await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
            history_journal_id=jid,
            history_source_kind=K, history_source_id=I, history_source_url=U))
    assert await conn.fetchval("SELECT count(*) FROM koi_memories WHERE rid = $1", SUCC) == 0


@pytest.mark.anyio
async def test_write_document_without_history_keys_touches_no_history_table(conn):
    claims_before = await conn.fetchval("SELECT count(*) FROM document_source_claim")
    edges_before = await conn.fetchval("SELECT count(*) FROM document_supersession")
    chunks, embs = _chunks("plain bytes")
    assert await _write_document(conn, SUCC, "plain bytes", chunks, embs, _history_meta()) == 1
    assert await conn.fetchval("SELECT count(*) FROM document_source_claim") == claims_before
    assert await conn.fetchval("SELECT count(*) FROM document_supersession") == edges_before
    assert await _superseded_at(conn, SUCC) is None
    # POSITIVE CONTROL: the row was written, so the counts above did see a real ingest.
    assert await conn.fetchval("SELECT count(*) FROM koi_memories WHERE rid = $1", SUCC) == 1


@pytest.mark.anyio
async def test_claim_cutover_refuses_a_staged_caller(conn):
    with pytest.raises(StagedWriteViolation):
        await _claim_cutover(conn, staged=True, markdown="x", source_kind=K, source_id=I,
                             source_url=U, document_rid=SUCC, content_hash=None,
                             history_policy=None)
    assert await conn.fetchval("SELECT count(*) FROM document_source_claim "
                               "WHERE document_rid = $1", SUCC) == 0


# =============================================================================
# plan-edit 23 — the two paths on which the pair does not run at all
# =============================================================================

def test_empty_content_claim_is_unreachable_through_both_call_sites():
    """Belt-and-braces, not "the enforcement": no input reaches _write_document empty."""
    from api.chunker import TextChunker
    chunker = TextChunker(chunk_size=500, chunk_overlap=50)
    for md in ["", " ", "\n\n\t ", "\xa0", "x", "hello world", "  padded  "]:
        reaches_write_document = bool(chunker.chunk_text(md))   # `if not chunks: return`
        assert not (reaches_write_document and not md.strip())
    # POSITIVE CONTROL: the chunker is not simply returning [] for everything.
    assert chunker.chunk_text("real bytes")


class _NullEmbedder:
    async def embed(self, text):
        return None


class _FixedChunker:
    def __init__(self, chunks):
        self.chunks = chunks

    def chunk_text(self, markdown):
        return self.chunks


@pytest.mark.anyio
@pytest.mark.parametrize("dry_run,chunks", [(True, [{"text": "real bytes"}]), (False, [])],
                         ids=["dry-run", "no-chunks"])
async def test_no_run_paths_through_the_call_site(conn, dry_run, chunks):
    """Both early returns in the real call site leave the edge pending, write nothing."""
    jid = await _seed_edge(conn, U, PRED)
    res = await ingest_document_rag_conn(
        conn, document_rid=SUCC, markdown="real bytes",
        source_meta=_history_meta(staged=True, history_journal_id=jid,
                                  history_source_kind=K, history_source_id=I,
                                  history_source_url=U),
        embedder=_NullEmbedder(), chunker=_FixedChunker(chunks), dry_run=dry_run)
    assert res["chunks_written"] == 0
    assert tuple(await conn.fetchrow(
        "SELECT state, successor_rid FROM document_supersession WHERE id = $1", jid)) == (
        "pending", None), "a no-run path touched the journal"
    assert await conn.fetchval("SELECT count(*) FROM koi_memories WHERE rid = $1", SUCC) == 0
    # POSITIVE CONTROL: the same call with chunks and no dry-run does write and PATCH.
    res = await ingest_document_rag_conn(
        conn, document_rid=SUCC, markdown="real bytes",
        source_meta=_history_meta(staged=True, history_journal_id=jid,
                                  history_source_kind=K, history_source_id=I,
                                  history_source_url=U),
        embedder=_NullEmbedder(), chunker=_FixedChunker([{"text": "real bytes"}]))
    assert res["chunks_written"] == 1
    assert await conn.fetchval(
        "SELECT successor_rid FROM document_supersession WHERE id = $1", jid) == SUCC


@pytest.mark.anyio
async def test_no_chunks_with_a_claim_triple_mints_no_claim(conn):
    """The call-site return, not EmptyContentClaim, is what stops an empty ingest."""
    res = await ingest_document_rag_conn(
        conn, document_rid=SUCC, markdown="   ",
        source_meta=_history_meta(history_source_kind=K, history_source_id=I,
                                  history_source_url=U),
        embedder=_NullEmbedder(), chunker=_FixedChunker([]))
    assert res["chunks_written"] == 0
    assert await conn.fetchval("SELECT count(*) FROM document_source_claim") == 0


# =============================================================================
# Journal state, self-edges, shared bytes
# =============================================================================

@pytest.mark.anyio
@pytest.mark.parametrize("state,refusal,accepted", [
    ("pending", None, True), ("failed", None, True),
    ("cancelled", None, False), ("refused", "X", False),
], ids=["pending", "failed", "cancelled", "refused"])
async def test_patch_respects_the_open_state_guard(conn, state, refusal, accepted):
    jid = await _seed_edge(conn, U, PRED, state=state, refusal=refusal)
    row = await conn.fetchrow(JOURNAL_SUCCESSOR_PATCH_SQL, jid, SUCC, K, I, U)
    assert (row is not None) is accepted
    assert (await conn.fetchval(
        "SELECT successor_rid FROM document_supersession WHERE id = $1", jid) == SUCC) is accepted


@pytest.mark.anyio
async def test_write_document_on_a_terminal_edge_discards_the_ingest(conn):
    jid = await _seed_edge(conn, U, PRED, state="cancelled")
    chunks, embs = _chunks("the new bytes")
    with pytest.raises(JournalSuccessorConflict):
        await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
            staged=True, history_journal_id=jid,
            history_source_kind=K, history_source_id=I, history_source_url=U))
    assert await conn.fetchval("SELECT count(*) FROM koi_memories WHERE rid = $1", SUCC) == 0
    assert await conn.fetchval(
        "SELECT count(*) FROM koi_memory_chunks WHERE document_rid = $1", SUCC) == 0


@pytest.mark.anyio
async def test_self_supersession_is_a_typed_conflict(conn):
    """Unchanged bytes re-mint the predecessor's rid; chk_dsup_selfedge must surface as
    JournalSuccessorConflict, not a raw asyncpg error, and roll the ingest back."""
    jid = await _seed_edge(conn, U, SUCC)          # predecessor == the rid this ingest makes
    chunks, embs = _chunks("the same bytes")
    with pytest.raises(JournalSuccessorConflict, match="SELF_SUPERSESSION"):
        await _write_document(conn, SUCC, "the same bytes", chunks, embs, _history_meta(
            staged=True, history_journal_id=jid,
            history_source_kind=K, history_source_id=I, history_source_url=U))
    assert await conn.fetchval("SELECT count(*) FROM koi_memories WHERE rid = $1", SUCC) == 0


@pytest.mark.anyio
async def test_staged_write_onto_a_live_rid_warns_instead_of_claiming_hidden(conn, caplog):
    await upsert_document_memory(conn, SUCC, "shared bytes", {"slug": "t"})   # already live
    jid = await _seed_edge(conn, U, PRED)
    chunks, embs = _chunks("shared bytes")
    caplog.set_level(logging.INFO, logger=ingest_document.logger.name)
    await _write_document(conn, SUCC, "shared bytes", chunks, embs, _history_meta(
        staged=True, history_journal_id=jid,
        history_source_kind=K, history_source_id=I, history_source_url=U))
    assert await _superseded_at(conn, SUCC) is None
    assert any("NOT hidden" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    # POSITIVE CONTROL: a fresh rid logs the hidden case, not the warning.
    caplog.clear()
    jid2 = await _seed_edge(conn, OTHER_URL, "document:" + "b" * 64)
    await _write_document(conn, PRED, "fresh bytes", *_chunks("fresh bytes"), _history_meta(
        staged=True, history_journal_id=jid2,
        history_source_kind=K, history_source_id=I, history_source_url=OTHER_URL))
    assert not any("NOT hidden" in r.getMessage() for r in caplog.records)


# =============================================================================
# The claim cutover: mint, stickiness, revival
# =============================================================================

@pytest.mark.anyio
async def test_first_ingest_mints_a_keep_claim(conn):
    chunks, embs = _chunks("the new bytes")
    await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
        history_source_kind=K, history_source_id=I, history_source_url=U))
    row = await conn.fetchrow(
        """SELECT document_rid, claim_state, history_policy, content_hash
             FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U)
    assert tuple(row) == (SUCC, "live", "keep", SUCC[9:])


@pytest.mark.anyio
async def test_policy_mismatch_refuses_and_discards_the_ingest(conn):
    await _seed_claimed_predecessor(conn, policy="drop")
    chunks, embs = _chunks("the new bytes")
    with pytest.raises(RuntimeError, match="POLICY_ASSERTION_MISMATCH"):
        await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
            history_policy="keep",
            history_source_kind=K, history_source_id=I, history_source_url=U))
    row = await conn.fetchrow(
        """SELECT document_rid, history_policy FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U)
    assert tuple(row) == (PRED, "drop"), "a refused run moved the claim or its policy"
    assert await conn.fetchval("SELECT count(*) FROM koi_memories WHERE rid = $1", SUCC) == 0
    # POSITIVE CONTROL: the matching assertion passes and advances the claim.
    await _write_document(conn, SUCC, "the new bytes", chunks, embs, _history_meta(
        history_policy="drop",
        history_source_kind=K, history_source_id=I, history_source_url=U))
    assert await conn.fetchval(
        """SELECT document_rid FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U) == SUCC


@pytest.mark.anyio
async def test_unasserted_policy_stays_sticky(conn):
    await _seed_claimed_predecessor(conn, policy="drop")
    await _write_document(conn, SUCC, "the new bytes", *_chunks("the new bytes"), _history_meta(
        history_source_kind=K, history_source_id=I, history_source_url=U))
    assert await conn.fetchval(
        """SELECT history_policy FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U) == "drop"


@pytest.mark.anyio
@pytest.mark.parametrize("prior", ["retired", "released", "live"])
async def test_cutover_revives_a_retired_or_released_claim(conn, caplog, prior):
    await _seed_claimed_predecessor(conn, claim_state=prior)
    caplog.set_level(logging.WARNING, logger=ingest_document.logger.name)
    await _write_document(conn, SUCC, "the new bytes", *_chunks("the new bytes"), _history_meta(
        history_source_kind=K, history_source_id=I, history_source_url=U))
    row = await conn.fetchrow(
        """SELECT document_rid, claim_state, retired_at, released_at FROM document_source_claim
            WHERE (source_kind,source_id,source_url)=($1,$2,$3)""", K, I, U)
    assert tuple(row) == (SUCC, "live", None, None)
    revived = [r.getMessage() for r in caplog.records if "revived from" in r.getMessage()]
    if prior == "live":
        assert revived == [], "a live claim was reported as revived"
    else:
        assert any(f"revived from {prior}" in m and PRED in m for m in revived)


# =============================================================================
# The reveal un-stamps the document's discourse moves (I11)
# =============================================================================

async def _seed_stamped_move(conn, rid):
    await conn.execute(
        """INSERT INTO session_discourse_moves
             (move_type, title, source_type, source_rid, superseded_at)
           VALUES ('claim', 't', 'document', $1, now())""", rid)


async def _stamped_moves(conn, rid):
    return await conn.fetchval(
        """SELECT count(*) FROM session_discourse_moves
            WHERE source_type = 'document' AND source_rid = $1
              AND superseded_at IS NOT NULL""", rid)


@pytest.mark.anyio
async def test_reveal_unstamps_discourse_moves(conn):
    await upsert_document_memory(conn, SUCC, "bytes A", {"slug": "t"})
    await conn.execute("UPDATE koi_memories SET superseded_at = now() WHERE rid = $1", SUCC)
    await _seed_stamped_move(conn, SUCC)
    assert await _stamped_moves(conn, SUCC) == 1          # POSITIVE CONTROL: it is stamped
    await upsert_document_memory(conn, SUCC, "bytes A", {"slug": "t"})
    assert await _superseded_at(conn, SUCC) is None
    assert await _stamped_moves(conn, SUCC) == 0, "I11: a live row kept stamped moves"


@pytest.mark.anyio
async def test_staged_retry_leaves_hidden_moves_stamped(conn):
    await upsert_document_memory(conn, SUCC, "bytes", {"slug": "t", "staged": True})
    await _seed_stamped_move(conn, SUCC)
    await upsert_document_memory(conn, SUCC, "bytes", {"slug": "t", "staged": True})
    assert await _superseded_at(conn, SUCC) is not None
    assert await _stamped_moves(conn, SUCC) == 1, "a hidden row's moves were un-stamped"


@pytest.mark.anyio
async def test_unstamp_is_skipped_where_migration_123_is_absent():
    """Outside the migration (the NUC, the bare test database) the column does not exist
    and the upsert must still succeed rather than abort the caller's transaction."""
    _conn = await asyncpg.connect(DB_URL)
    try:
        assert await _conn.fetchval("SELECT current_database()") != "personal_koi"
        has = await _conn.fetchval(
            """SELECT EXISTS (SELECT 1 FROM pg_attribute
                               WHERE attrelid = to_regclass('session_discourse_moves')
                                 AND attname = 'superseded_at' AND NOT attisdropped)""")
        if has:
            pytest.skip("this database already has migration 123 applied")
        tx = _conn.transaction()
        await tx.start()
        try:
            await upsert_document_memory(_conn, SUCC, "bytes", {"slug": "t"})
            assert await _superseded_at(_conn, SUCC) is None
        finally:
            await tx.rollback()
    finally:
        await _conn.close()


# =============================================================================
# CLI wiring — the flags reach the guard before any embedding spend or DB work
# =============================================================================

@pytest.mark.parametrize("flags,expected", [
    (["--history-journal-id", "5", "--history-claim", f"{K}|{I}|{U}"],
     "--history-journal-id was given without --staged"),
    (["--staged", "--history-journal-id", "5"],
     "--history-journal-id requires --history-claim"),
    (["--history-claim", "only|two"],
     "--history-claim must be 'source_kind|source_id|source_url'"),
    (["--staged"], "--staged was given without --history-journal-id"),
    (["--history-policy", "keep"], "would never be checked"),
    (["--tier", "standard", "--staged", "--history-journal-id", "5",
      "--history-claim", f"{K}|{I}|{U}"], "--staged requires --tier rag"),
], ids=["journal-without-staged", "journal-without-claim", "malformed-claim",
        "staged-without-journal", "policy-without-claim", "staged-deeper-tier"])
def test_cli_refuses_misuse_before_any_work(tmp_path, flags, expected):
    src = tmp_path / "doc.md"
    src.write_text("some real bytes\n")
    env = dict(os.environ, INGEST_SOURCE_ROOT=str(tmp_path), OPENAI_API_KEY="",
               POSTGRES_URL=DB_URL)
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ingest_document.py"),
         "--source-path", str(src), "--tier", "rag", *flags],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 1, proc.stderr
    assert expected in proc.stderr
    # The refusal comes before the OPENAI_API_KEY check, i.e. before any spend.
    assert "OPENAI_API_KEY not set" not in proc.stderr


def test_cli_reaches_the_key_check_without_history_flags(tmp_path):
    """POSITIVE CONTROL for the test above: with no history flags the same invocation
    gets past source_meta and stops at the (deliberately empty) API key."""
    src = tmp_path / "doc.md"
    src.write_text("some real bytes\n")
    env = dict(os.environ, INGEST_SOURCE_ROOT=str(tmp_path), OPENAI_API_KEY="",
               POSTGRES_URL=DB_URL)
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ingest_document.py"),
         "--source-path", str(src), "--tier", "rag"],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 1
    assert "OPENAI_API_KEY not set" in proc.stderr


# =============================================================================
# In-process wiring — valid flags reach source_meta (no DB, no network)
# =============================================================================

@pytest.mark.anyio
async def test_ingest_path_spreads_valid_history_flags(monkeypatch, tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("some real bytes\n")
    seen = {}

    class _Pool:
        async def close(self):
            pass

    async def _fake_pool(*a, **kw):
        return _Pool()

    async def _fake_rag(pool, **kw):
        seen["source_meta"] = kw["source_meta"]
        return {"document_rid": kw["document_rid"], "chunks_written": 1,
                "chunks_total": 1, "null_embeds": 0}

    monkeypatch.setenv("INGEST_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(ingest_document, "OPENAI_API_KEY", "x")
    monkeypatch.setattr(ingest_document, "OpenAIEmbeddingProvider", lambda **kw: object())
    monkeypatch.setattr(ingest_document.asyncpg, "create_pool", _fake_pool)
    monkeypatch.setattr(ingest_document, "ingest_document_rag", _fake_rag)

    common = dict(source_path=str(src), tier="rag", slug=None, name=None, source_url=None,
                  retrieval_method=None, group_id="test-history")
    await ingest_document.ingest_path(**common, staged=True, history_journal_id=5,
                                      history_claim=f"{K}|{I}|{U}")
    meta = seen["source_meta"]
    assert meta["staged"] is True and meta["history_journal_id"] == 5
    assert _history_claim_triple(meta) == (K, I, U)

    await ingest_document.ingest_path(**common, history_claim=f"{K}|{I}|{U}",
                                      history_policy="drop")
    assert seen["source_meta"]["history_policy"] == "drop"
    assert seen["source_meta"]["staged"] is False

    # POSITIVE CONTROL: with no history flags no history key appears.
    await ingest_document.ingest_path(**common)
    assert not any(k == "staged" or k.startswith("history_") for k in seen["source_meta"])


def test_main_passes_every_history_flag(monkeypatch, tmp_path):
    seen = {}

    async def _fake_ingest_path(**kw):
        seen.update(kw)
        return {"document_rid": "document:x"}

    monkeypatch.setattr(ingest_document, "ingest_path", _fake_ingest_path)
    monkeypatch.setattr(sys, "argv", [
        "ingest_document.py", "--source-path", str(tmp_path / "doc.md"), "--tier", "rag",
        "--staged", "--history-journal-id", "5", "--history-claim", f"{K}|{I}|{U}",
        "--history-policy", "keep"])
    ingest_document.main()
    assert (seen["staged"], seen["history_journal_id"], seen["history_claim"],
            seen["history_policy"]) == (True, 5, f"{K}|{I}|{U}", "keep")
