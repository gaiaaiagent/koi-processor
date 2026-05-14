"""Shared helper for emitting federation domain events from mutation endpoints.

Usage in routers:
    from api.federation_events import emit_domain_event
    await emit_domain_event("entity", "NEW", rid, payload)

For knowledge-domain emits that require idempotent subscriber behavior:
    event_id = uuid.uuid4()
    await emit_domain_event(
        "knowledge_episode", "NEW", rid, payload,
        payload_event_id=str(event_id),
    )
The caller-supplied event_id is BOTH passed to the queue (for queue-level
dedup via UNIQUE (source_node, event_id, target_node)) AND injected into
payload as `_federation_event_id` (for subscriber-level dedup via the
federation_applied_events table — see migration 100).

The event_queue is set during app startup by personal_ingest_api.py.
If federation is not enabled, calls are silently no-ops.

Feature flag KOI_FEDERATE_KNOWLEDGE gates the three knowledge-related
domains only ('knowledge_episode', 'knowledge_fact', 'document_entity_link').
Other domains (entity, claim, attestation, commitment, etc.) are not
affected by the flag. Default off — flip to "true" per node after
subscriber handlers are deployed.
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_event_queue = None

# Knowledge-domain emits gated by KOI_FEDERATE_KNOWLEDGE.
KNOWLEDGE_DOMAINS = frozenset({
    "knowledge_episode",
    "knowledge_fact",
    "document_entity_link",
})


def set_event_queue(eq):
    """Called once during app startup to wire the event queue."""
    global _event_queue
    _event_queue = eq


def _knowledge_federation_enabled() -> bool:
    """Read KOI_FEDERATE_KNOWLEDGE flag (re-read every call so env flips take
    effect without process restart; cheap — single os.environ lookup).
    """
    return os.getenv("KOI_FEDERATE_KNOWLEDGE", "false").lower() == "true"


async def emit_domain_event(
    domain: str,
    event_type: str,
    rid: str,
    payload: Dict[str, Any],
    target_node: Optional[str] = None,
    payload_event_id: Optional[str] = None,
):
    """Queue a domain federation event. No-op if federation is not enabled.

    payload_event_id (optional): caller-supplied UUID string. When provided,
      (a) used as the queue's event_id (queue dedups on UNIQUE
          (source_node, event_id, target_node)), and
      (b) injected into payload as `_federation_event_id` so subscriber-side
          idempotency tracking in federation_applied_events can dedup on the
          same key. Required for knowledge-domain emits that hit additive
          subscriber paths (e.g., document_entity_link mention_count).
    """
    if _event_queue is None:
        return

    # Feature-flag gate for knowledge domains (publish side).
    if domain in KNOWLEDGE_DOMAINS and not _knowledge_federation_enabled():
        return

    # Inject the federation event_id into payload contents so the subscriber
    # can read it back without depending on queue-level fields.
    effective_payload = payload
    if payload_event_id is not None:
        effective_payload = {**payload, "_federation_event_id": payload_event_id}

    try:
        await _event_queue.add(
            event_type=event_type,
            rid=rid,
            contents={
                "_koi_domain": domain,
                "payload": effective_payload,
            },
            target_node=target_node,
            event_id=payload_event_id,
        )
    except Exception as e:
        logger.warning(f"federation_events.emit failed domain={domain} rid={rid}: {e}")
