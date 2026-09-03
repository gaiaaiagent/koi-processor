#!/usr/bin/env python3
"""TELUS Gemma-3-27B client for deep session extraction (Phase C, Decision 109).

Env config (loaded by caller via deep_extract.env):
  TELUS_API_TOKEN      — bearer token (required; healthcheck fails without it)
  TELUS_GEMMA_URL      — endpoint, default https://gemma-3-27b-it-0b50s.paas.ai.telus.com/v1/chat/completions
  TELUS_RPS            — requests/sec throttle, default 1.0
  TELUS_CONCURRENCY    — max concurrent calls, default 1

CLI:
  --healthcheck        — probe endpoint reachability; exit 0 if reachable + authed
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import httpx


DEFAULT_URL = "https://gemma-3-27b-it-0b50s.paas.ai.telus.com/v1/chat/completions"
DEFAULT_MODEL = "google/gemma-3-27b-it"
BACKOFF_SECONDS_429 = 60
MAX_RETRIES_429 = 3
READ_TIMEOUT_SECONDS = 120


class TelusUnavailable(RuntimeError):
    """Raised when TELUS endpoint is unreachable or unauthenticated."""


class TelusTimeout(RuntimeError):
    """Raised when TELUS request exceeds read timeout."""


@dataclass
class TelusConfig:
    url: str
    token: str
    rps: float
    concurrency: int

    @classmethod
    def from_env(cls) -> "TelusConfig":
        token = os.environ.get("TELUS_API_TOKEN", "").strip()
        return cls(
            url=os.environ.get("TELUS_GEMMA_URL", DEFAULT_URL),
            token=token,
            rps=float(os.environ.get("TELUS_RPS", "1.0")),
            concurrency=int(os.environ.get("TELUS_CONCURRENCY", "1")),
        )


class RateLimiter:
    """Simple token-bucket limiter: enforces max RPS + max concurrency."""

    def __init__(self, rps: float, concurrency: int):
        self._min_interval = 1.0 / max(rps, 0.001)
        self._last_call = 0.0
        self._sem = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self._sem.acquire()
        async with self._lock:
            wait = self._last_call + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
        return self

    async def __aexit__(self, *exc):
        self._sem.release()


async def healthcheck(config: Optional[TelusConfig] = None, timeout: float = 10.0) -> dict:
    """Probe the endpoint. Returns dict with status; raises TelusUnavailable on failure.

    Strategy: POST a tiny chat completion (3-token max). A 401/403 means the
    endpoint is up but token is missing/wrong; network errors mean unreachable.
    """
    cfg = config or TelusConfig.from_env()
    if not cfg.token:
        raise TelusUnavailable("TELUS_API_TOKEN not set")
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 3,
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {cfg.token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(cfg.url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise TelusUnavailable(f"TELUS network error: {e}") from e
    if resp.status_code == 200:
        return {"status": "healthy", "http_status": 200}
    if resp.status_code in (401, 403):
        raise TelusUnavailable(f"TELUS auth failed (status {resp.status_code})")
    raise TelusUnavailable(f"TELUS returned {resp.status_code}: {resp.text[:200]}")


async def extract(prompt: str, config: Optional[TelusConfig] = None, rate_limiter: Optional[RateLimiter] = None) -> str:
    """Send prompt to TELUS Gemma, return raw completion string.

    Raises TelusUnavailable on non-recoverable errors or exhausted retries.
    Raises TelusTimeout on read timeout.
    """
    cfg = config or TelusConfig.from_env()
    if not cfg.token:
        raise TelusUnavailable("TELUS_API_TOKEN not set")
    limiter = rate_limiter or RateLimiter(cfg.rps, cfg.concurrency)

    # NOTE: omit response_format — the vLLM deployment of gemma-3-27b-it
    # responds with an empty `{}` object when response_format.type=json_object
    # is set. Orchestrator extracts JSON body via first `{` / last `}` which
    # handles markdown fences gracefully.
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8192,
        "temperature": 0.1,
    }
    headers = {"Authorization": f"Bearer {cfg.token}", "Content-Type": "application/json"}

    attempt = 0
    while attempt <= MAX_RETRIES_429:
        async with limiter:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=10.0)) as client:
                    resp = await client.post(cfg.url, json=payload, headers=headers)
            except httpx.ReadTimeout as e:
                raise TelusTimeout(f"TELUS read timeout after {READ_TIMEOUT_SECONDS}s") from e
            except httpx.HTTPError as e:
                raise TelusUnavailable(f"TELUS network error: {e}") from e

        if resp.status_code == 200:
            data = resp.json()
            # OpenAI-compatible chat response
            choices = data.get("choices") or []
            if not choices:
                raise TelusUnavailable(f"TELUS returned empty choices: {data}")
            return choices[0].get("message", {}).get("content", "")
        if resp.status_code == 429:
            attempt += 1
            if attempt > MAX_RETRIES_429:
                raise TelusUnavailable(f"TELUS 429 after {MAX_RETRIES_429} retries")
            await asyncio.sleep(BACKOFF_SECONDS_429)
            continue
        # Non-429 error: don't retry
        raise TelusUnavailable(f"TELUS returned {resp.status_code}: {resp.text[:500]}")

    raise TelusUnavailable("TELUS: unreachable retry exit")


def _cli() -> int:
    parser = argparse.ArgumentParser(description="TELUS Gemma client")
    parser.add_argument("--healthcheck", action="store_true", help="Probe endpoint, exit 0 if healthy")
    args = parser.parse_args()

    if args.healthcheck:
        try:
            result = asyncio.run(healthcheck())
            print(json.dumps(result))
            return 0
        except TelusUnavailable as e:
            print(f"TELUS unhealthy: {e}", file=sys.stderr)
            return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
