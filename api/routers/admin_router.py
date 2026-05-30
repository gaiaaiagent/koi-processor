"""Admin endpoints — entity merge / redirect.

Provides ``POST /entities/merge`` (service-token gated): merge a *loser* entity
into a *survivor*, rewiring every entity-URI reference in one transaction, then
TOMBSTONING the loser (``entity_registry.merged_into = survivor``) instead of
hard-deleting it. The loser is never deleted because the ON DELETE CASCADE FKs
on ``entity_relationships`` / ``pending_relationships`` would otherwise wipe the
very rows the merge just rewired.

Also exposes ``GET /entities/{uri}/resolve`` to follow a merged_into redirect
chain to the canonical live URI.

Routes are prefix-relative — prefix "/entities" is applied at mount in
personal_ingest_api.py.

A "retype" (e.g. a Concept that should be a Person) is expressed as a merge:
the entity type is encoded in the fuseki_uri prefix (…entity:organization-… vs
…entity:person-…), so merging into a correctly-typed survivor URI re-types it.

Reference surface (verified live against personal_koi 2026-05-29, all keyed by
entity_registry.fuseki_uri):

  Collision-prone (rewire-then-dedup; have a UNIQUE constraint a blind rewrite
  could violate):
    * entity_relationships  UNIQUE(subject_uri, predicate, object_uri)  [FK CASCADE]
    * document_entity_links UNIQUE(document_rid, entity_uri)            [additive mention_count]
    * pending_relationships UNIQUE(COALESCE(subject_uri,''),
                                   COALESCE(object_uri,''), predicate,
                                   raw_unknown_label, unknown_side)     [FK CASCADE]

  Plain UPDATE (no UNIQUE on the URI columns):
    knowledge_facts(subject_uri,object_uri), claims(entity_uri,claimant_uri,
    operator_uri), claim_attestations(reviewer_uri), entity_rid_mappings(
    canonical_uri), intent_registry(entity_uri,publisher_uri), task_registry(
    owner_uri,project_uri), commitments(pledger_uri,evidence_uri),
    commitment_pools(steward_uri,bioregion_uri), signals(subject_uri),
    requirements(subject_uri), assertion_history(subject,object_uri)*,
    koi_extraction_records(subject_uri,object_uri)*    (* RDF mirror, empty today)

  Array columns (array_replace):
    claim_attestations.evidence_uris, task_registry.collaborator_uris

  JSONB metadata (text-replace of the embedded URI):
    claims.metadata + the other *.metadata columns + entity_registry.metadata
    (loser row excluded)

  Aliases: the loser's entity_text + aliases are unioned into the survivor's
  aliases so the survivor stays findable by the loser's name(s).

See plan ~/.claude/plans/koi-entity-merge-fact-retraction-handoff.md; task #3146.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth_deps import make_service_token_auth

logger = logging.getLogger(__name__)


# Plain text columns holding an entity fuseki_uri, with no UNIQUE constraint on
# the column — a blind UPDATE loser->survivor cannot violate a constraint.
# (table, column)
_PLAIN_REF_COLS: List[tuple] = [
    ("knowledge_facts", "subject_uri"),
    ("knowledge_facts", "object_uri"),
    ("claims", "entity_uri"),
    ("claims", "claimant_uri"),
    ("claims", "operator_uri"),         # RESTRICT FK -> entity_registry; survivor exists, safe
    ("claim_attestations", "reviewer_uri"),  # RESTRICT FK; safe
    ("entity_rid_mappings", "canonical_uri"),
    ("intent_registry", "entity_uri"),
    ("intent_registry", "publisher_uri"),
    ("task_registry", "owner_uri"),
    ("task_registry", "project_uri"),
    ("commitments", "pledger_uri"),
    ("commitments", "evidence_uri"),
    ("commitment_pools", "steward_uri"),
    ("commitment_pools", "bioregion_uri"),
    ("signals", "subject_uri"),
    ("requirements", "subject_uri"),
    ("assertion_history", "subject"),       # RDF mirror (empty today); column is `subject`
    ("assertion_history", "object_uri"),
    ("koi_extraction_records", "subject_uri"),
    ("koi_extraction_records", "object_uri"),
]

# text[] columns holding entity URIs — rewrite with array_replace.
_ARRAY_REF_COLS: List[tuple] = [
    ("claim_attestations", "evidence_uris"),
    ("task_registry", "collaborator_uris"),
]

# jsonb columns that may embed an entity URI as a string value/substring.
# Rewritten with a guarded text-REPLACE (URIs carry a unique hash suffix, so the
# loser URI is never a substring of a different entity's URI — REPLACE is safe).
_JSONB_METADATA_COLS: List[tuple] = [
    ("claims", "metadata"),
    ("claim_attestations", "metadata"),
    ("commitments", "metadata"),
    ("commitment_pools", "metadata"),
    ("intent_registry", "metadata"),
    ("requirements", "metadata"),
    ("signals", "metadata"),
    ("task_registry", "metadata"),
]


def _count(status: str) -> int:
    """Parse the affected-row count out of an asyncpg command status string.

    asyncpg returns e.g. ``'UPDATE 5'`` / ``'DELETE 2'`` from ``conn.execute``.
    """
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        return 0


class EntityMergeRequest(BaseModel):
    survivor_uri: str = Field(..., description="fuseki_uri of the entity to KEEP")
    loser_uri: str = Field(..., description="fuseki_uri of the entity to merge in + tombstone")
    merged_by: Optional[str] = Field(
        None, description="Audit actor; defaults to the authenticated identity")
    dry_run: bool = Field(
        False,
        description="If true, perform the full rewire inside a transaction, report "
                    "the per-table counts, then ROLL BACK — nothing is committed.")


class EntityMergeResponse(BaseModel):
    survivor_uri: str
    loser_uri: str
    dry_run: bool
    applied: bool                         # True only if committed
    already_merged: bool = False
    rewired: Dict[str, Any] = {}          # per-table {rewired, deduped, merged, self_loops_deleted}
    total_refs_rewired: int = 0
    survivor_aliases_after: Optional[List[str]] = None
    merge_log_id: Optional[int] = None
    message: Optional[str] = None


def create_router(pool) -> APIRouter:
    """Return an APIRouter for admin (entity-merge) endpoints."""
    router = APIRouter(tags=["admin"])
    require_service_auth = make_service_token_auth(pool)

    async def _do_merge(conn, survivor: str, loser: str, merged_by: str) -> Dict[str, Any]:
        """Rewire every reference loser->survivor inside the caller's transaction.

        Returns the per-table counts dict. Assumes validation already passed
        (both exist, survivor live, loser not already merged, survivor != loser).
        """
        rewired: Dict[str, Any] = {}

        # --- 0. Union the loser's name + aliases into the survivor's aliases so
        #        the survivor stays findable by the loser's name(s). Mirrors the
        #        register_redirect_alias idiom in api/mediawiki_ingest.py.
        loser_row = await conn.fetchrow(
            "SELECT entity_text, COALESCE(aliases, '{}') AS aliases "
            "FROM entity_registry WHERE fuseki_uri = $1", loser)
        loser_text = loser_row["entity_text"] if loser_row else None
        loser_aliases = list(loser_row["aliases"]) if loser_row else []
        add_aliases = [a for a in ([loser_text] + loser_aliases) if a]
        await conn.execute("""
            UPDATE entity_registry
            SET aliases = (
                SELECT ARRAY(
                    SELECT DISTINCT e FROM unnest(
                        array_cat(COALESCE(aliases, '{}'), $2::text[])
                    ) AS e
                    WHERE e IS NOT NULL AND e <> ''
                )
            )
            WHERE fuseki_uri = $1
        """, survivor, add_aliases)

        # --- 1. entity_relationships — rewire-then-dedup on UNIQUE(subj,pred,obj).
        #        NULL object_uri rows are never deduped: Postgres UNIQUE treats
        #        NULLs as distinct, so a blind rewrite of them can't collide.
        er: Dict[str, int] = {}
        er["subject_deduped"] = _count(await conn.execute("""
            DELETE FROM entity_relationships el
            WHERE el.subject_uri = $1
              AND EXISTS (SELECT 1 FROM entity_relationships e2
                          WHERE e2.subject_uri = $2
                            AND e2.predicate = el.predicate
                            AND e2.object_uri = el.object_uri)
        """, loser, survivor))
        er["subject_rewired"] = _count(await conn.execute(
            "UPDATE entity_relationships SET subject_uri = $2 WHERE subject_uri = $1",
            loser, survivor))
        er["object_deduped"] = _count(await conn.execute("""
            DELETE FROM entity_relationships el
            WHERE el.object_uri = $1
              AND EXISTS (SELECT 1 FROM entity_relationships e2
                          WHERE e2.object_uri = $2
                            AND e2.predicate = el.predicate
                            AND e2.subject_uri = el.subject_uri)
        """, loser, survivor))
        er["object_rewired"] = _count(await conn.execute(
            "UPDATE entity_relationships SET object_uri = $2 WHERE object_uri = $1",
            loser, survivor))
        er["self_loops_deleted"] = _count(await conn.execute(
            "DELETE FROM entity_relationships WHERE subject_uri = $1 AND object_uri = $1",
            survivor))
        rewired["entity_relationships"] = er

        # --- 2. document_entity_links — UNIQUE(document_rid, entity_uri),
        #        mention_count is additive. Fold the loser's count into a
        #        colliding survivor row, drop the colliding loser rows, then
        #        rewrite the rest.
        del_: Dict[str, int] = {}
        del_["merged"] = _count(await conn.execute("""
            UPDATE document_entity_links s
            SET mention_count = COALESCE(s.mention_count, 0) + COALESCE(l.mention_count, 0)
            FROM document_entity_links l
            WHERE l.entity_uri = $1 AND s.entity_uri = $2
              AND s.document_rid = l.document_rid
        """, loser, survivor))
        del_["deduped"] = _count(await conn.execute("""
            DELETE FROM document_entity_links l
            WHERE l.entity_uri = $1
              AND EXISTS (SELECT 1 FROM document_entity_links s
                          WHERE s.entity_uri = $2 AND s.document_rid = l.document_rid)
        """, loser, survivor))
        del_["rewired"] = _count(await conn.execute(
            "UPDATE document_entity_links SET entity_uri = $2 WHERE entity_uri = $1",
            loser, survivor))
        rewired["document_entity_links"] = del_

        # --- 3. pending_relationships — rewire-then-dedup. The UNIQUE index
        #        COALESCEs NULL subject/object to '', so dedup must match with
        #        the same COALESCE semantics (NULLs collide).
        pr: Dict[str, int] = {}
        pr["subject_deduped"] = _count(await conn.execute("""
            DELETE FROM pending_relationships el
            WHERE el.subject_uri = $1
              AND EXISTS (SELECT 1 FROM pending_relationships e2
                          WHERE e2.subject_uri = $2
                            AND e2.predicate = el.predicate
                            AND COALESCE(e2.object_uri,'') = COALESCE(el.object_uri,'')
                            AND e2.raw_unknown_label = el.raw_unknown_label
                            AND e2.unknown_side = el.unknown_side)
        """, loser, survivor))
        pr["subject_rewired"] = _count(await conn.execute(
            "UPDATE pending_relationships SET subject_uri = $2 WHERE subject_uri = $1",
            loser, survivor))
        pr["object_deduped"] = _count(await conn.execute("""
            DELETE FROM pending_relationships el
            WHERE el.object_uri = $1
              AND EXISTS (SELECT 1 FROM pending_relationships e2
                          WHERE e2.object_uri = $2
                            AND e2.predicate = el.predicate
                            AND COALESCE(e2.subject_uri,'') = COALESCE(el.subject_uri,'')
                            AND e2.raw_unknown_label = el.raw_unknown_label
                            AND e2.unknown_side = el.unknown_side)
        """, loser, survivor))
        pr["object_rewired"] = _count(await conn.execute(
            "UPDATE pending_relationships SET object_uri = $2 WHERE object_uri = $1",
            loser, survivor))
        pr["self_loops_deleted"] = _count(await conn.execute(
            "DELETE FROM pending_relationships WHERE subject_uri = $1 AND object_uri = $1",
            survivor))
        rewired["pending_relationships"] = pr

        # --- 4. Plain text columns (no UNIQUE on the URI column).
        for table, col in _PLAIN_REF_COLS:
            n = _count(await conn.execute(
                f"UPDATE {table} SET {col} = $2 WHERE {col} = $1", loser, survivor))
            if n:
                rewired.setdefault("plain", {})[f"{table}.{col}"] = n

        # --- 5. Array columns (text[]).
        for table, col in _ARRAY_REF_COLS:
            n = _count(await conn.execute(
                f"UPDATE {table} SET {col} = array_replace({col}, $1, $2) "
                f"WHERE $1 = ANY({col})", loser, survivor))
            if n:
                rewired.setdefault("arrays", {})[f"{table}.{col}"] = n

        # --- 6. JSONB metadata text-replace (guarded by LIKE).
        for table, col in _JSONB_METADATA_COLS:
            n = _count(await conn.execute(
                f"UPDATE {table} SET {col} = REPLACE({col}::text, $1, $2)::jsonb "
                f"WHERE {col}::text LIKE '%' || $1 || '%'", loser, survivor))
            if n:
                rewired.setdefault("jsonb_metadata", {})[f"{table}.{col}"] = n
        # entity_registry.metadata for OTHER rows (never the loser's own row).
        n = _count(await conn.execute(
            "UPDATE entity_registry SET metadata = REPLACE(metadata::text, $1, $2)::jsonb "
            "WHERE fuseki_uri <> $1 AND metadata::text LIKE '%' || $1 || '%'",
            loser, survivor))
        if n:
            rewired.setdefault("jsonb_metadata", {})["entity_registry.metadata"] = n

        # --- 7. Tombstone the loser (NEVER hard-delete — FK CASCADE would wipe
        #        the relationships just rewired).
        rewired["loser_tombstoned"] = _count(await conn.execute(
            "UPDATE entity_registry SET merged_into = $2, merged_at = NOW(), merged_by = $3 "
            "WHERE fuseki_uri = $1 AND merged_into IS NULL", loser, survivor, merged_by))

        return rewired

    def _total(rewired: Dict[str, Any]) -> int:
        total = 0
        for v in rewired.values():
            if isinstance(v, dict):
                total += sum(x for x in v.values() if isinstance(x, int))
            elif isinstance(v, int):
                total += v
        return total

    @router.post("/merge", response_model=EntityMergeResponse)
    async def merge_entities(
        body: EntityMergeRequest,
        identity: str = Depends(require_service_auth),
    ):
        survivor = body.survivor_uri
        loser = body.loser_uri
        merged_by = body.merged_by or identity

        if survivor == loser:
            raise HTTPException(status_code=400,
                                detail="survivor_uri and loser_uri are identical")

        async with pool.acquire() as conn:
            s_row = await conn.fetchrow(
                "SELECT fuseki_uri, merged_into FROM entity_registry WHERE fuseki_uri = $1",
                survivor)
            l_row = await conn.fetchrow(
                "SELECT fuseki_uri, merged_into FROM entity_registry WHERE fuseki_uri = $1",
                loser)
            if s_row is None:
                raise HTTPException(status_code=404, detail=f"survivor not found: {survivor}")
            if l_row is None:
                raise HTTPException(status_code=404, detail=f"loser not found: {loser}")

            # Survivor must be live (not itself a tombstone) — merging into a
            # tombstone would create a stale chain.
            if s_row["merged_into"] is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"survivor is itself merged into {s_row['merged_into']}; "
                           f"merge into the live entity instead")

            # Idempotency on the loser.
            if l_row["merged_into"] is not None:
                if l_row["merged_into"] == survivor:
                    prior = await conn.fetchrow(
                        "SELECT id, rewired FROM entity_merge_log "
                        "WHERE loser_uri = $1 AND survivor_uri = $2 "
                        "ORDER BY merged_at DESC LIMIT 1", loser, survivor)
                    # asyncpg returns the jsonb `rewired` column as a JSON string;
                    # decode it back to a dict for the response model.
                    prior_rewired: Dict[str, Any] = {}
                    if prior and prior["rewired"]:
                        raw = prior["rewired"]
                        prior_rewired = json.loads(raw) if isinstance(raw, str) else raw
                    return EntityMergeResponse(
                        survivor_uri=survivor, loser_uri=loser, dry_run=body.dry_run,
                        applied=False, already_merged=True,
                        rewired=prior_rewired,
                        merge_log_id=(prior["id"] if prior else None),
                        message="loser already merged into survivor (no-op)")
                raise HTTPException(
                    status_code=409,
                    detail=f"loser already merged into {l_row['merged_into']} "
                           f"(not {survivor})")

            tx = conn.transaction()
            await tx.start()
            try:
                rewired = await _do_merge(conn, survivor, loser, merged_by)
                survivor_aliases = await conn.fetchval(
                    "SELECT aliases FROM entity_registry WHERE fuseki_uri = $1", survivor)

                merge_log_id: Optional[int] = None
                if not body.dry_run:
                    merge_log_id = await conn.fetchval("""
                        INSERT INTO entity_merge_log
                            (survivor_uri, loser_uri, rewired, merged_by)
                        VALUES ($1, $2, $3::jsonb, $4)
                        RETURNING id
                    """, survivor, loser, json.dumps(rewired), merged_by)

                if body.dry_run:
                    await tx.rollback()
                else:
                    await tx.commit()
            except HTTPException:
                await tx.rollback()
                raise
            except Exception as e:
                await tx.rollback()
                logger.exception("entity merge failed loser=%s survivor=%s", loser, survivor)
                raise HTTPException(status_code=500, detail=f"merge failed: {e}")

        total = _total(rewired)
        logger.info(
            "entity_merge %s loser=%s -> survivor=%s total_refs=%d merge_log_id=%s by=%s",
            "DRY_RUN" if body.dry_run else "APPLIED",
            loser, survivor, total, merge_log_id, merged_by)
        return EntityMergeResponse(
            survivor_uri=survivor, loser_uri=loser, dry_run=body.dry_run,
            applied=(not body.dry_run), already_merged=False,
            rewired=rewired, total_refs_rewired=total,
            survivor_aliases_after=list(survivor_aliases) if survivor_aliases else [],
            merge_log_id=merge_log_id,
            message=("dry run — rolled back, nothing committed"
                     if body.dry_run else "merge applied"))

    @router.get("/{uri:path}/resolve")
    async def resolve_entity_redirect(uri: str):
        """Follow the merged_into chain to the canonical live URI (max 16 hops)."""
        async with pool.acquire() as conn:
            current = uri
            chain = [current]
            for _ in range(16):
                row = await conn.fetchrow(
                    "SELECT merged_into FROM entity_registry WHERE fuseki_uri = $1", current)
                if row is None:
                    if current == uri:
                        raise HTTPException(status_code=404, detail=f"entity not found: {uri}")
                    break
                if row["merged_into"] is None:
                    return {"input_uri": uri, "canonical_uri": current,
                            "redirected": current != uri, "chain": chain}
                current = row["merged_into"]
                if current in chain:  # cycle guard
                    raise HTTPException(status_code=409,
                                        detail=f"merged_into cycle detected: {chain + [current]}")
                chain.append(current)
            raise HTTPException(status_code=409,
                                detail=f"merged_into chain too long (>16 hops): {chain}")

    return router
