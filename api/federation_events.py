"""Shared helper for emitting federation domain events from mutation endpoints.

Usage in routers:
    from api.federation_events import emit_domain_event
    await emit_domain_event("entity", "NEW", rid, payload)

The event_queue is set during app startup by personal_ingest_api.py.
If federation is not enabled, calls are silently no-ops.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_event_queue = None


def set_event_queue(eq):
    """Called once during app startup to wire the event queue."""
    global _event_queue
    _event_queue = eq


async def emit_domain_event(
    domain: str,
    event_type: str,
    rid: str,
    payload: Dict[str, Any],
    target_node: Optional[str] = None,
):
    """Queue a domain federation event. No-op if federation is not enabled."""
    if _event_queue is None:
        return
    try:
        await _event_queue.add(
            event_type=event_type,
            rid=rid,
            contents={
                "_koi_domain": domain,
                "payload": payload,
            },
            target_node=target_node,
        )
    except Exception as e:
        logger.warning(f"federation_events.emit failed domain={domain} rid={rid}: {e}")
