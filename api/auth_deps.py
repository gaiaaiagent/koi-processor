"""Reusable FastAPI auth dependencies for pool-backed routers.

Why this exists:
  ``src/services/auth_service.get_authorized_user`` already validates session
  Bearer tokens, but opens a fresh ``asyncpg`` connection per request because
  auth_service owns its own DB lifecycle. Routers that are constructed with a
  shared ``asyncpg.Pool`` (claims, intent, etc.) should validate through the
  same pool to avoid per-request connect/disconnect overhead.

  This module exposes factories — pass a pool, get typed FastAPI dependencies
  bound to it:

      # At router-creation time
      require_auth = make_require_auth(pool)

      # On an endpoint
      @router.post("/", status_code=201)
      async def create_claim(
          req: ClaimCreateRequest,
          user_email: str = Depends(require_auth),
      ):
          # user_email is guaranteed — 401 is raised before the body runs
          ...

Three factories:
  * ``make_require_auth(pool)`` — hard gate; 401 on missing / invalid / revoked / expired.
  * ``make_optional_auth(pool)`` — soft; returns the email if a valid token is
    present, otherwise None. Use on reads that want audit context without
    blocking anonymous access.
  * ``make_service_token_auth(pool, env_var)`` — accepts either a valid
    session token OR a fixed service token from env (for CI / backend-to-
    backend callers that don't have an OAuth identity).

All three call the same validator so there's a single source of truth for
what "authenticated" means.

SECURITY NOTES
  * Token hashing matches ``auth_service.hash_token`` (SHA-256 hex) — the DB
    only ever stores hashes, never raw tokens.
  * ``session_tokens`` rows are checked for ``revoked_at`` and ``expires_at``
    on every request.
  * The ``Authorization`` header parser is strict (``Bearer <token>``, two
    whitespace-separated parts, case-insensitive scheme).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Awaitable, Callable, Optional

from asyncpg.pool import Pool
from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: token validation (single source of truth)
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    """SHA-256 hash — must match src/services/auth_service.hash_token."""
    return hashlib.sha256(token.encode()).hexdigest()


def _parse_bearer(authorization: Optional[str]) -> Optional[str]:
    """Return the raw token from an Authorization header, or None if malformed."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


async def _validate_session_token(token: str, pool: Pool) -> str:
    """Validate a Bearer session token against ``session_tokens``.

    Returns the authenticated user's email.
    Raises :class:`HTTPException` (401) on invalid / revoked / expired tokens.
    """
    token_hash = _hash_token(token)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_email, expires_at, revoked_at
            FROM session_tokens
            WHERE token_hash = $1
            """,
            token_hash,
        )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid session token")
    if row["revoked_at"]:
        raise HTTPException(status_code=401, detail="Session token has been revoked")
    if row["expires_at"].timestamp() < time.time():
        raise HTTPException(status_code=401, detail="Session token has expired")
    return row["user_email"]


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------

RequireAuthDep = Callable[..., Awaitable[str]]
OptionalAuthDep = Callable[..., Awaitable[Optional[str]]]


def make_require_auth(pool: Pool) -> RequireAuthDep:
    """Factory: FastAPI dependency that requires a valid session Bearer token.

    Returns the authenticated user's email. Raises 401 on any failure.
    """

    async def require_auth(authorization: Optional[str] = Header(None)) -> str:
        if not authorization:
            raise HTTPException(status_code=401, detail="Authorization header required")
        token = _parse_bearer(authorization)
        if token is None:
            raise HTTPException(status_code=401, detail="Invalid authorization header format")
        return await _validate_session_token(token, pool)

    return require_auth


def make_optional_auth(pool: Pool) -> OptionalAuthDep:
    """Factory: FastAPI dependency that optionally binds a session to the request.

    Returns ``user_email`` if a valid token is present, otherwise ``None``.
    Never raises. Use on read endpoints where anonymous access is allowed but
    an authenticated identity (when present) should be recorded for audit.
    """

    async def optional_auth(authorization: Optional[str] = Header(None)) -> Optional[str]:
        token = _parse_bearer(authorization)
        if token is None:
            return None
        try:
            return await _validate_session_token(token, pool)
        except HTTPException:
            return None

    return optional_auth


def make_service_token_auth(
    pool: Pool,
    env_var: str = "KOI_CLAIMS_SERVICE_TOKEN",
    service_identity: str = "service:claims-service",
) -> RequireAuthDep:
    """Factory: dependency that accepts EITHER a valid session Bearer OR a
    fixed service token read from environment.

    Use for backend-to-backend callers (CI jobs, scheduled anchoring, the
    claims-service daemon itself) that need to write but don't have an OAuth
    identity.

    The service token — if set — is compared constant-time against the header.
    When the service token matches, the dependency returns
    ``service_identity`` so the endpoint can still populate
    ``operator_uri`` / ``created_by`` with a sensible value.
    """

    import secrets as _secrets

    async def service_or_session(authorization: Optional[str] = Header(None)) -> str:
        token = _parse_bearer(authorization)
        if token is None:
            raise HTTPException(status_code=401, detail="Bearer token required")

        # Constant-time compare against service token, if configured.
        service_token = os.getenv(env_var, "")
        if service_token and _secrets.compare_digest(token, service_token):
            return service_identity

        return await _validate_session_token(token, pool)

    return service_or_session


__all__ = [
    "make_require_auth",
    "make_optional_auth",
    "make_service_token_auth",
    "RequireAuthDep",
    "OptionalAuthDep",
]
