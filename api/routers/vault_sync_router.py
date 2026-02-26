"""Vault sync management endpoints (trigger, status, pause, resume).

Wraps the VaultSyncManager from api/vault_sync.py to provide REST control
over bidirectional markdown sync between KOI-net peers.
Only included when caps.vault_sync is True.

Note: mounted at /koi-net/vault-sync to group with other KOI-net endpoints.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


# -- Response models ---------------------------------------------------------

class VaultSyncStatusResponse(BaseModel):
    """Current vault sync manager status and metrics."""
    running: bool = False
    paused: bool = False
    scan_interval_s: int = 60
    files_scanned: int = 0
    events_queued: int = 0
    events_applied: int = 0
    conflicts_created: int = 0
    scans_completed: int = 0
    watcher_enabled: bool = False
    last_error: Optional[str] = None


class VaultSyncTriggerResponse(BaseModel):
    """Result of manually triggering a vault sync."""
    status: str  # "triggered", "already_running", "paused", "error"
    message: str = ""
    events_queued: int = 0


# -- Router factory ----------------------------------------------------------

# Reference to the VaultSyncManager instance, set by the startup profile.
_vault_sync_manager = None


def set_vault_sync_manager(manager):
    """Called by the startup profile to inject the VaultSyncManager instance."""
    global _vault_sync_manager
    _vault_sync_manager = manager


def create_router(pool, caps):
    """Return an APIRouter for vault sync endpoints.

    Only included when caps.vault_sync is True.  Mounted at
    /koi-net/vault-sync by the app wiring layer.

    Parameters
    ----------
    pool : asyncpg.Pool
        Database connection pool.
    caps : Capabilities
        Runtime capabilities (vault_sync flag).
    """
    router = APIRouter(prefix="/koi-net/vault-sync", tags=["vault-sync"])

    @router.get("/status", response_model=VaultSyncStatusResponse)
    async def vault_sync_status():
        """Return vault sync manager status and metrics.

        Includes scan counts, event counts, conflict counts, and whether
        the file watcher is active.
        """
        if _vault_sync_manager is None:
            return VaultSyncStatusResponse(running=False)

        metrics = _vault_sync_manager.metrics
        return VaultSyncStatusResponse(
            running=True,
            paused=getattr(_vault_sync_manager, "_paused", False),
            scan_interval_s=getattr(
                _vault_sync_manager, "scan_interval", 60
            ),
            files_scanned=metrics.files_scanned,
            events_queued=metrics.events_queued,
            events_applied=metrics.events_applied,
            conflicts_created=metrics.conflicts_created,
            scans_completed=metrics.scans_completed,
            watcher_enabled=metrics.watcher_enabled,
        )

    @router.post("/trigger", response_model=VaultSyncTriggerResponse)
    async def vault_sync_trigger():
        """Manually trigger an immediate vault sync scan.

        Calls VaultSyncManager.trigger_sync() to run a scan outside the
        normal polling interval.
        """
        if _vault_sync_manager is None:
            raise HTTPException(
                status_code=503,
                detail="Vault sync manager not initialized",
            )

        result = await _vault_sync_manager.trigger_sync()
        return VaultSyncTriggerResponse(
            status=result.get("status", "triggered"),
            message=result.get("message", ""),
            events_queued=result.get("events_queued", 0),
        )

    @router.post("/pause")
    async def vault_sync_pause():
        """Pause the vault sync polling loop.

        The manager stays initialized but stops scanning until resumed.
        """
        if _vault_sync_manager is None:
            raise HTTPException(
                status_code=503,
                detail="Vault sync manager not initialized",
            )

        _vault_sync_manager._paused = True
        return {"status": "paused"}

    @router.post("/resume")
    async def vault_sync_resume():
        """Resume the vault sync polling loop after a pause."""
        if _vault_sync_manager is None:
            raise HTTPException(
                status_code=503,
                detail="Vault sync manager not initialized",
            )

        _vault_sync_manager._paused = False
        return {"status": "resumed"}

    return router
