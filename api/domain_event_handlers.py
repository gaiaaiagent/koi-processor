"""Consumer-side handlers for domain federation events.

Each handler applies a federated domain event (entity, claim, attestation,
commitment, commitment_pool, task) to the local database via UPSERT semantics.

Called from koi_poller._process_event() when contents contain a "_koi_domain" marker.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from api.utils import parse_ts

logger = logging.getLogger(__name__)


async def apply_domain_event(
    conn,
    domain: str,
    rid: str,
    event_type: str,
    payload: Dict[str, Any],
    source_node: str,
):
    """Dispatch to the appropriate domain handler."""
    handlers = {
        "entity": _apply_entity,
        "claim": _apply_claim,
        "attestation": _apply_attestation,
        "commitment": _apply_commitment,
        "commitment_pool": _apply_commitment_pool,
        "task": _apply_task,
        "intent": _apply_intent,
    }
    handler = handlers.get(domain)
    if not handler:
        logger.warning(f"Unknown _koi_domain '{domain}' in event {rid}")
        return

    if event_type == "FORGET":
        await _handle_forget(conn, domain, rid, payload)
        return

    await handler(conn, rid, event_type, payload, source_node)


async def _handle_forget(conn, domain: str, rid: str, payload: Dict[str, Any]):
    """Handle FORGET events by deleting the row from the domain table."""
    table_rid_map = {
        "entity": ("entity_registry", "fuseki_uri"),
        "claim": ("claims", "claim_rid"),
        "attestation": ("claim_attestations", "attestation_rid"),
        "commitment": ("commitments", "commitment_rid"),
        "commitment_pool": ("commitment_pools", "pool_rid"),
        "task": ("task_registry", "task_key"),
        "intent": ("intent_discovery_cache", "intent_rid"),
    }
    table, col = table_rid_map.get(domain, (None, None))
    if not table:
        return
    rid_val = payload.get(col, rid)
    await conn.execute(f"DELETE FROM {table} WHERE {col} = $1", rid_val)
    logger.info(f"domain.forget domain={domain} rid={rid_val}")


async def _apply_entity(conn, rid: str, event_type: str, payload: Dict[str, Any], source_node: str):
    """UPSERT entity into entity_registry."""
    fuseki_uri = payload.get("fuseki_uri", rid)
    entity_text = payload.get("entity_text", "")
    entity_type = payload.get("entity_type", "")
    normalized_text = payload.get("normalized_text", entity_text.lower().strip())
    aliases = payload.get("aliases", [])
    metadata = payload.get("metadata", {})

    await conn.execute("""
        INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text, aliases, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        ON CONFLICT (fuseki_uri) DO UPDATE SET
            entity_text = EXCLUDED.entity_text,
            entity_type = EXCLUDED.entity_type,
            normalized_text = EXCLUDED.normalized_text,
            aliases = EXCLUDED.aliases,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
    """, fuseki_uri, entity_text, entity_type, normalized_text,
        aliases or [], json.dumps(metadata) if isinstance(metadata, dict) else metadata)

    # Upsert relationships if included
    relationships = payload.get("relationships", [])
    for rel in relationships:
        subj = rel.get("subject_uri", "")
        pred = rel.get("predicate", "")
        obj = rel.get("object_uri", "")
        conf = rel.get("confidence", 1.0)
        src = rel.get("source", "federation")
        if subj and pred and obj:
            # Use a savepoint so FK violations don't abort the outer transaction
            # (asyncpg puts the PG transaction in error state on any failed SQL)
            try:
                await conn.execute("SAVEPOINT rel_insert")
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (subject_uri, predicate, object_uri) DO UPDATE SET
                        confidence = EXCLUDED.confidence,
                        source = EXCLUDED.source,
                        updated_at = NOW()
                """, subj, pred, obj, conf, src)
                await conn.execute("RELEASE SAVEPOINT rel_insert")
            except Exception as e:
                await conn.execute("ROLLBACK TO SAVEPOINT rel_insert")
                # FK violations are expected if related entities haven't arrived yet
                logger.debug(f"domain.entity.rel_skip pred={pred} err={e}")

    logger.info(f"domain.entity.apply uri={fuseki_uri} type={entity_type} new_or_update={event_type}")


async def _apply_claim(conn, rid: str, event_type: str, payload: Dict[str, Any], source_node: str):
    """UPSERT claim into claims table."""
    claim_rid = payload.get("claim_rid", rid)

    await conn.execute("""
        INSERT INTO claims (
            claim_rid, entity_uri, claimant_uri, statement, claim_type,
            verification, source_document, ai_confidence, content_hash,
            supersedes_rid, metadata, created_by, operator_uri
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13)
        ON CONFLICT (claim_rid) DO UPDATE SET
            verification = EXCLUDED.verification,
            content_hash = EXCLUDED.content_hash,
            metadata = EXCLUDED.metadata,
            operator_uri = EXCLUDED.operator_uri,
            updated_at = NOW()
    """,
        claim_rid,
        payload.get("entity_uri"),
        payload.get("claimant_uri"),
        payload.get("statement"),
        payload.get("claim_type", "ecological"),
        payload.get("verification", "self_reported"),
        payload.get("source_document"),
        payload.get("ai_confidence"),
        payload.get("content_hash"),
        payload.get("supersedes_rid"),
        json.dumps(payload.get("metadata", {})),
        payload.get("created_by"),
        payload.get("operator_uri"),
    )

    # Append state transition if included (idempotent via pre-check)
    st = payload.get("state_transition")
    if st:
        await _append_state_log(
            conn, "claim_state_log", "claim_rid", claim_rid,
            st.get("from_state"), st.get("to_state"), st.get("actor"),
            st.get("reason"), st.get("metadata"), st.get("created_at"),
        )

    logger.info(f"domain.claim.apply rid={claim_rid}")


async def _apply_attestation(conn, rid: str, event_type: str, payload: Dict[str, Any], source_node: str):
    """UPSERT attestation into claim_attestations."""
    att_rid = payload.get("attestation_rid", rid)
    claim_rid = payload.get("claim_rid")
    reviewer_uri = payload.get("reviewer_uri")

    await conn.execute("""
        INSERT INTO claim_attestations (
            attestation_rid, claim_rid, reviewer_uri, verdict, rationale,
            evidence_uris, content_hash, metadata
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        ON CONFLICT (claim_rid, reviewer_uri) DO UPDATE SET
            verdict = EXCLUDED.verdict,
            rationale = EXCLUDED.rationale,
            evidence_uris = EXCLUDED.evidence_uris,
            content_hash = EXCLUDED.content_hash,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
    """,
        att_rid, claim_rid, reviewer_uri,
        payload.get("verdict", "pending"),
        payload.get("rationale"),
        payload.get("evidence_uris", []),
        payload.get("content_hash"),
        json.dumps(payload.get("metadata", {})),
    )

    logger.info(f"domain.attestation.apply rid={att_rid} claim={claim_rid}")


async def _apply_commitment(conn, rid: str, event_type: str, payload: Dict[str, Any], source_node: str):
    """UPSERT commitment into commitments table."""
    commitment_rid = payload.get("commitment_rid", rid)

    await conn.execute("""
        INSERT INTO commitments (
            commitment_rid, pledger_uri, title, description, offer_type,
            quantity, unit, validity_start, validity_end, state,
            evidence_uri, metadata, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::commitment_state, $11, $12::jsonb, $13)
        ON CONFLICT (commitment_rid) DO UPDATE SET
            state = EXCLUDED.state,
            evidence_uri = EXCLUDED.evidence_uri,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
    """,
        commitment_rid,
        payload.get("pledger_uri"),
        payload.get("title"),
        payload.get("description"),
        payload.get("offer_type", "labor"),
        payload.get("quantity"),
        payload.get("unit"),
        parse_ts(payload.get("validity_start")),
        parse_ts(payload.get("validity_end")),
        payload.get("state", "PROPOSED"),
        payload.get("evidence_uri"),
        json.dumps(payload.get("metadata", {})),
        payload.get("created_by"),
    )

    # Append state log if included
    st = payload.get("state_transition")
    if st:
        await _append_state_log(
            conn, "commitment_state_log", "commitment_rid", commitment_rid,
            st.get("from_state"), st.get("to_state"), st.get("actor"),
            st.get("reason"), st.get("metadata"), st.get("created_at"),
        )

    logger.info(f"domain.commitment.apply rid={commitment_rid}")


async def _apply_commitment_pool(conn, rid: str, event_type: str, payload: Dict[str, Any], source_node: str):
    """UPSERT commitment pool. V1: pool creation only (no pledge membership)."""
    pool_rid = payload.get("pool_rid", rid)

    await conn.execute("""
        INSERT INTO commitment_pools (
            pool_rid, name, description, steward_uri, bioregion_uri,
            activation_threshold_pct, activation_threshold_count,
            demurrage_rate_monthly, state, metadata, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
        ON CONFLICT (pool_rid) DO UPDATE SET
            state = EXCLUDED.state,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
    """,
        pool_rid,
        payload.get("name"),
        payload.get("description"),
        payload.get("steward_uri"),
        payload.get("bioregion_uri"),
        payload.get("activation_threshold_pct"),
        payload.get("activation_threshold_count"),
        payload.get("demurrage_rate_monthly"),
        payload.get("state", "forming"),
        json.dumps(payload.get("metadata", {})),
        payload.get("created_by"),
    )

    logger.info(f"domain.pool.apply rid={pool_rid}")


async def _apply_task(conn, rid: str, event_type: str, payload: Dict[str, Any], source_node: str):
    """UPSERT task into task_registry."""
    task_key = payload.get("task_key", rid)

    await conn.execute("""
        INSERT INTO task_registry (
            task_key, uuid, title, status, priority,
            due_date, start_date, wait_until, context, effort,
            owner_uri, project_uri, collaborator_uris, blocked_by,
            source_note, source_type, vault_path, tags,
            validity_start, validity_end
        ) VALUES (
            $1, $2, $3,
            COALESCE($4, 'inbox'), COALESCE($5, 'medium'),
            $6::date, $7::date, $8::date, $9, $10,
            $11, $12, $13, $14,
            $15, COALESCE($16, 'meeting'), $17, $18,
            $19, $20
        )
        ON CONFLICT (task_key) DO UPDATE SET
            uuid = COALESCE(EXCLUDED.uuid, task_registry.uuid),
            title = EXCLUDED.title,
            status = CASE WHEN $4 IS NULL THEN task_registry.status ELSE $4 END,
            priority = CASE WHEN $5 IS NULL THEN task_registry.priority ELSE $5 END,
            due_date = COALESCE(EXCLUDED.due_date, task_registry.due_date),
            start_date = COALESCE(EXCLUDED.start_date, task_registry.start_date),
            wait_until = COALESCE(EXCLUDED.wait_until, task_registry.wait_until),
            context = COALESCE(EXCLUDED.context, task_registry.context),
            effort = COALESCE(EXCLUDED.effort, task_registry.effort),
            owner_uri = COALESCE(EXCLUDED.owner_uri, task_registry.owner_uri),
            project_uri = COALESCE(EXCLUDED.project_uri, task_registry.project_uri),
            collaborator_uris = CASE WHEN array_length(EXCLUDED.collaborator_uris, 1) > 0
                                    THEN EXCLUDED.collaborator_uris
                                    ELSE task_registry.collaborator_uris END,
            blocked_by = CASE WHEN array_length(EXCLUDED.blocked_by, 1) > 0
                              THEN EXCLUDED.blocked_by
                              ELSE task_registry.blocked_by END,
            source_note = COALESCE(EXCLUDED.source_note, task_registry.source_note),
            source_type = COALESCE(EXCLUDED.source_type, task_registry.source_type),
            vault_path = COALESCE(EXCLUDED.vault_path, task_registry.vault_path),
            tags = CASE WHEN array_length(EXCLUDED.tags, 1) > 0
                        THEN EXCLUDED.tags ELSE task_registry.tags END,
            validity_start = COALESCE(EXCLUDED.validity_start, task_registry.validity_start),
            validity_end   = COALESCE(EXCLUDED.validity_end,   task_registry.validity_end),
            updated_at = NOW()
    """,
        task_key,
        payload.get("uuid"),
        payload.get("title"),
        payload.get("status"),
        payload.get("priority"),
        payload.get("due_date"),
        payload.get("start_date"),
        payload.get("wait_until"),
        payload.get("context"),
        payload.get("effort"),
        payload.get("owner_uri"),
        payload.get("project_uri"),
        payload.get("collaborator_uris", []),
        payload.get("blocked_by", []),
        payload.get("source_note"),
        payload.get("source_type"),
        payload.get("vault_path"),
        payload.get("tags", []),
        parse_ts(payload.get("validity_start")),
        parse_ts(payload.get("validity_end")),
    )

    logger.info(f"domain.task.apply key={task_key}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _append_state_log(
    conn,
    table: str,
    rid_col: str,
    rid_val: str,
    from_state: Optional[str],
    to_state: Optional[str],
    actor: Optional[str],
    reason: Optional[str],
    metadata: Any = None,
    created_at: Optional[str] = None,
):
    """Append a state log entry, skipping duplicates by (rid, to_state, created_at)."""
    if not to_state:
        return

    # Parse created_at for dedup check
    ts = None
    if created_at:
        try:
            ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = None

    # Dedup: skip if this exact transition already exists
    if ts:
        exists = await conn.fetchval(
            f"SELECT 1 FROM {table} WHERE {rid_col} = $1 AND to_state = $2 AND created_at = $3",
            rid_val, to_state, ts,
        )
        if exists:
            return

    meta_json = json.dumps(metadata) if isinstance(metadata, dict) else metadata

    if table == "commitment_state_log":
        # commitment_state_log uses commitment_state enum for from_state/to_state
        if ts:
            await conn.execute(f"""
                INSERT INTO {table} ({rid_col}, from_state, to_state, actor, reason, metadata, created_at)
                VALUES ($1, $2::commitment_state, $3::commitment_state, $4, $5, $6::jsonb, $7)
            """, rid_val, from_state, to_state, actor, reason, meta_json, ts)
        else:
            await conn.execute(f"""
                INSERT INTO {table} ({rid_col}, from_state, to_state, actor, reason, metadata)
                VALUES ($1, $2::commitment_state, $3::commitment_state, $4, $5, $6::jsonb)
            """, rid_val, from_state, to_state, actor, reason, meta_json)
    else:
        if ts:
            await conn.execute(f"""
                INSERT INTO {table} ({rid_col}, from_state, to_state, actor, reason, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            """, rid_val, from_state, to_state, actor, reason, meta_json, ts)
        else:
            await conn.execute(f"""
                INSERT INTO {table} ({rid_col}, from_state, to_state, actor, reason, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """, rid_val, from_state, to_state, actor, reason, meta_json)


# _parse_ts() lifted to api.utils.parse_ts so task_router / commitment_router /
# intent_router can share the same parser. Backward-compat alias preserved for
# any in-tree callers of the old private name.
_parse_ts = parse_ts


async def _apply_intent(conn, rid: str, event_type: str, payload: Dict[str, Any], source_node: str):
    """UPSERT remote intent discovery projection into intent_discovery_cache.

    Only discovery-level fields are stored — no publisher_contact,
    source_excerpt, priority, tags, or match criteria cross node boundaries.
    """
    intent_rid = payload.get("intent_rid", rid)

    await conn.execute("""
        INSERT INTO intent_discovery_cache (
            intent_rid, source_node, intent_type, status,
            landscape_group, visibility, asset_offered, asset_wanted,
            quantity
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (intent_rid) DO UPDATE SET
            status = EXCLUDED.status,
            visibility = EXCLUDED.visibility,
            asset_offered = EXCLUDED.asset_offered,
            asset_wanted = EXCLUDED.asset_wanted,
            quantity = EXCLUDED.quantity,
            updated_at = NOW()
    """,
        intent_rid,
        source_node or "unknown",
        payload.get("intent_type", "OFFER"),
        payload.get("status", "active"),
        payload.get("landscape_group", "unknown"),
        payload.get("visibility", "local"),
        payload.get("asset_offered"),
        payload.get("asset_wanted"),
        payload.get("quantity"),
    )
