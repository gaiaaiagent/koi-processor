#!/usr/bin/env python3
"""KOI-native sustained-write: koi_memories → /knowledge/episodes API.

Phase 2 of plan `koi-graph-consolidation-retire-graphiti.md` (2026-04-29).
Writes Spore canon-rebuild artifacts (decision-record, foundation, architecture)
directly to PostgreSQL via the `/knowledge/episodes` endpoint. Single-substrate
path that eliminated the dual-attribution problem and replaced the prior
`graphiti_sustained_write.py` (now retired alongside the FalkorDB sidecar at
2026-04-30 Wave 1 close-out).

Per plan §Decision-gate D1+D2:
  - ADR-Entity → entity_type=SpecDoc (URI shape: orn:personal-koi.entity:specdoc-<slug>-<hash>)
  - Pre-resolve via POST /entity/resolve with type_hint="SpecDoc" before
    POST /knowledge/episodes; if is_new, write entity row directly with
    correct type (Tier 3 of the resolver returns URI but does NOT persist).
  - Session entities (one per ADR's authoring session) get the same
    pre-resolve + write-on-is_new pattern with type_hint="Session" or fallback.

Per plan §Assumptions:
  - decision-record kinds get AUTHORED_WITHIN edges (session map below).
  - foundation/architecture/etc. get NO AUTHORED_WITHIN; Phase 7 may add
    RELATES_TO edges from `relates_to:` frontmatter.
  - Pre-flight health check: GET /health; fail-fast on non-healthy.
  - On 4xx: log + skip; on 5xx: retry once after 2s; on conn err: fail-fast.

Usage:
  --sample-rids R1,R2,...     Run sample-gate over explicit RIDs.
  --batch-id ID               Override default batch_id stamp.
  --doc-kinds A,B,C           Override default allowlist.
  --dry-run                   Audit only; no writes.

Phase 4 sample-gate invocation:
  python3 scripts/koi_sustained_write.py \\
      --sample-rids "<5 RIDs>" \\
      --batch-id koi_canon_sample_2026_04_29
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import psycopg

# --- Production scheme ---
GROUP_ID = "koi_canon_v1"
DEFAULT_BATCH_ID = "koi_canon_2026_04_29"
# Phase 7 (2026-04-29): expanded from batch-1 (decision-record + foundation +
# architecture) to include pattern / spec / connection / synthesis / methodology.
# Skip: plan (high-churn ephemeral), work-order (wrong shape — task tracking,
# not canon). foundations (plural) and learning-field-artifact treated as
# legacy spellings; not added.
DEFAULT_DOC_KINDS = (
    "decision-record",
    "foundation",
    "architecture",
    "pattern",
    "spec",
    "connection",
    "synthesis",
    "methodology",
)

DB_URL = "dbname=personal_koi"
KOI_BASE_URL = os.environ.get("KOI_API_ENDPOINT", "http://localhost:8351")
# /knowledge/episodes auth: the CLAIMS service token (server-side require_service_auth
# checks KOI_CLAIMS_SERVICE_TOKEN). Must be present in this script's env post-gate;
# sending it is a harmless no-op until the :8351 episodes-gate lands.
KOI_CLAIMS_SERVICE_TOKEN = os.environ.get("KOI_CLAIMS_SERVICE_TOKEN", "")

# --- Strand-D session map (Spore canon-rebuild parent orchestrators) ---
SESSION_BC = "bc5c284d-2d1b-4ba0-9730-d83006480c52"
SESSION_585 = "585633a5-238b-402a-8ad4-80206269ce55"
UNKNOWN_SESSION = "00000000-0000-0000-0000-000000000000"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def derive_episode_name(rid: str) -> str:
    """`doc-scanner:spore:docs/research/canon-decisions/0080-foo.md` →
    `spore_ADR_0080_foo` for canon-decisions; generic fallback otherwise.

    Matches graphiti_sustained_write.py:derive_episode_name semantics for
    parity with yesterday's bench ground-truth.
    """
    m = re.match(
        r"doc-scanner:([^:]+):docs/research/canon-decisions/(\d{4}[a-z]?)-(.+)\.md",
        rid,
    )
    if m:
        repo, num, slug = m.groups()
        repo_short = {
            "spore": "spore",
            "intelligence-commons": "ic",
            "poietic-match": "pm",
        }.get(repo, repo.replace("-", "_"))
        slug_safe = re.sub(r"[^a-z0-9_]+", "_", slug.lower()).strip("_")
        return f"{repo_short}_ADR_{num}_{slug_safe}"

    m2 = re.match(r"doc-scanner:([^:]+):(.+)\.md", rid)
    if m2:
        repo, rel = m2.groups()
        repo_short = {
            "spore": "spore",
            "intelligence-commons": "ic",
            "poietic-match": "pm",
        }.get(repo, repo.replace("-", "_"))
        slug_safe = re.sub(r"[^a-z0-9_]+", "_", rel.lower()).strip("_")
        return f"{repo_short}_{slug_safe}"

    return rid.replace(":", "_").replace("/", "_").replace("-", "_").replace(".md", "")


def specdoc_label_for(rid: str) -> str:
    """Label used as the SpecDoc entity_text and resolver lookup label.

    Convention: `<repo_short>.adr-<num>-<slug>` for canon-decisions;
    `<repo_short>.<rel-stem>` for foundations/architecture/etc.
    Distinct from episode_name (which uses underscores) so SpecDoc URIs
    look like the existing `spec:bkc.project-vision` aesthetic.
    """
    m = re.match(
        r"doc-scanner:([^:]+):docs/research/canon-decisions/(\d{4}[a-z]?)-(.+)\.md",
        rid,
    )
    if m:
        repo, num, slug = m.groups()
        repo_short = {
            "spore": "spore",
            "intelligence-commons": "ic",
            "poietic-match": "pm",
        }.get(repo, repo.replace("-", "_"))
        return f"{repo_short}.adr-{num}-{slug}"

    m2 = re.match(r"doc-scanner:([^:]+):(.+)\.md", rid)
    if m2:
        repo, rel = m2.groups()
        repo_short = {
            "spore": "spore",
            "intelligence-commons": "ic",
            "poietic-match": "pm",
        }.get(repo, repo.replace("-", "_"))
        rel_clean = rel.replace("docs/", "").replace("/", ".")
        return f"{repo_short}.{rel_clean}"

    return rid.replace(":", ".").replace("/", ".").replace(".md", "")


def session_label_for(session_uuid: str) -> str:
    """Stable label for a session entity."""
    return f"claude-code session {session_uuid}"


def sessions_for_adr(repo: str, adr_num: int) -> list[str]:
    """ADR↔parent-session mapping (per Spore CLAUDE.md session-history table).

    Mirrors graphiti_sustained_write.py:sessions_uuid_for for parity with
    yesterday's bench. ADRs outside the hardcoded mapping get the all-zero
    UNKNOWN_SESSION placeholder (per plan §Assumptions §Session attribution).
    """
    if repo == "spore":
        if adr_num <= 71:
            return [SESSION_BC]
        if 72 <= adr_num <= 73:
            return [SESSION_BC, SESSION_585]
        return [SESSION_585, SESSION_BC]
    if repo == "intelligence-commons":
        if adr_num <= 19:
            return [SESSION_BC]
        return [SESSION_585, SESSION_BC]
    if repo == "poietic-match":
        if adr_num <= 15:
            return [SESSION_BC]
        return [SESSION_585, SESSION_BC]
    return [SESSION_BC]


# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight + DB
# ─────────────────────────────────────────────────────────────────────────────


def preflight_health(client: httpx.Client) -> dict[str, Any]:
    """Pre-flight: GET /health; raise on any non-healthy state."""
    try:
        r = client.get(f"{KOI_BASE_URL}/health", timeout=5.0)
    except httpx.RequestError as e:
        raise SystemExit(
            f"PREFLIGHT FAIL: KOI API unreachable at {KOI_BASE_URL}: {e}\n"
            f"  error_code: substrate_unavailable"
        )
    if r.status_code != 200:
        raise SystemExit(
            f"PREFLIGHT FAIL: /health returned {r.status_code}; "
            f"error_code: substrate_unavailable"
        )
    data = r.json()
    if data.get("status") != "healthy":
        raise SystemExit(f"PREFLIGHT FAIL: status={data.get('status')!r}")
    if data.get("database") != "connected":
        raise SystemExit(f"PREFLIGHT FAIL: database={data.get('database')!r}")
    if not data.get("embedding_available"):
        raise SystemExit("PREFLIGHT FAIL: embedding_available=false")
    return data


def fetch_rows(
    rid_filter: Optional[list[str]] = None,
    doc_kinds: tuple[str, ...] = DEFAULT_DOC_KINDS,
) -> list[tuple]:
    """Pull rows from `koi_memories` per plan §Assumptions § koi_memories row contract."""
    if rid_filter:
        sql = """
            SELECT rid,
                   content->>'title' AS title,
                   content->>'text' AS text,
                   created_at,
                   metadata->>'doc_kind' AS doc_kind,
                   metadata->>'repo' AS repo
            FROM koi_memories
            WHERE rid = ANY(%s)
            ORDER BY rid
        """
        params = (rid_filter,)
    else:
        sql = """
            SELECT rid,
                   content->>'title' AS title,
                   content->>'text' AS text,
                   created_at,
                   metadata->>'doc_kind' AS doc_kind,
                   metadata->>'repo' AS repo
            FROM koi_memories
            WHERE metadata->>'doc_kind' = ANY(%s)
            ORDER BY rid
        """
        params = (list(doc_kinds),)

    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# Resolver + entity-registry direct-write helpers
# ─────────────────────────────────────────────────────────────────────────────


def resolve_entity_uri(
    client: httpx.Client, label: str, type_hint: str, persist: bool = True
) -> tuple[str, bool, bool]:
    """POST /entity/resolve with type_hint; return (uri, is_new, persisted).

    Wave 2 B2 (2026-04-30): resolver-persistence asymmetry resolved at the
    API. Pass `persist=true` (default) so the resolver commits the new row
    to entity_registry server-side before returning. The previous workaround
    of direct-INSERT via `ensure_entity_row()` is no longer required.

    Returns (uri, is_new, persisted). When is_new=False, persisted=False
    (no work needed; row already existed). When is_new=True and persist=True,
    persisted=True iff store_new_entity succeeded server-side.
    """
    r = client.post(
        f"{KOI_BASE_URL}/entity/resolve",
        json={"label": label, "type_hint": type_hint, "persist": persist},
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        # Resolver returned no candidate (rare; defensive)
        raise RuntimeError(f"resolver returned no candidate for label={label!r}")
    cand = candidates[0]
    return cand["uri"], bool(data.get("is_new")), bool(data.get("persisted"))


def ensure_entity_row(
    conn, uri: str, label: str, entity_type: str, batch_id: str
) -> bool:
    """DEPRECATED — kept for backwards-compat only. Use resolve_entity_uri(persist=True)
    which performs the same INSERT server-side via store_new_entity().

    Returns True if a row was newly inserted; False if it already existed.
    """
    normalized = label.lower().strip()
    metadata_json = json.dumps({"batch_id": batch_id, "source": "koi_sustained_write"})
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity_registry
                (fuseki_uri, entity_text, normalized_text, entity_type,
                 source, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (fuseki_uri) DO NOTHING
            RETURNING id
            """,
            (uri, label, normalized, entity_type, "koi_sustained_write", metadata_json),
        )
        return cur.fetchone() is not None


# ─────────────────────────────────────────────────────────────────────────────
# Episode write (per row)
# ─────────────────────────────────────────────────────────────────────────────


def post_episode(
    client: httpx.Client, payload: dict, log_fn
) -> Optional[dict]:
    """POST /knowledge/episodes with retry-once on 5xx, skip on 4xx, fail-fast on conn err."""
    url = f"{KOI_BASE_URL}/knowledge/episodes"
    for attempt in (1, 2):
        try:
            r = client.post(url, json=payload, timeout=60.0)
        except httpx.RequestError as e:
            log_fn({"event": "episode_conn_err", "err": str(e)[:300]})
            raise SystemExit(
                f"FATAL: connection error to {url}: {e}\n"
                f"  error_code: substrate_unavailable"
            )
        if r.status_code in (200, 201):
            return r.json()
        if 400 <= r.status_code < 500:
            log_fn(
                {
                    "event": "episode_4xx_skip",
                    "status": r.status_code,
                    "body": r.text[:300],
                }
            )
            return None
        # 5xx — retry once
        if attempt == 1:
            log_fn(
                {
                    "event": "episode_5xx_retry",
                    "status": r.status_code,
                    "body": r.text[:300],
                }
            )
            time.sleep(2.0)
            continue
        log_fn(
            {
                "event": "episode_5xx_fail",
                "status": r.status_code,
                "body": r.text[:300],
            }
        )
        return None
    return None


def ingest_one(
    client: httpx.Client,
    row: tuple,
    batch_id: str,
    log_fn,
    dry_run: bool = False,
) -> dict:
    """Ingest a single koi_memories row.

    For decision-record kinds: pre-resolve+persist SpecDoc + Session entities
    via /entity/resolve?persist=true, then POST /knowledge/episodes with
    AUTHORED_WITHIN facts. For other kinds: POST episode with empty facts
    (RELATES_TO from frontmatter is Phase 7 work).

    Wave 2 B2 (2026-04-30): direct DB connection no longer required —
    persistence happens server-side via the resolver. Caller no longer
    needs to pass a psycopg connection.
    """
    rid, title, text, created_at, doc_kind, repo = row
    ref_time = (
        created_at
        if (created_at and created_at.tzinfo)
        else (created_at.replace(tzinfo=timezone.utc) if created_at else datetime.now(timezone.utc))
    )
    ep_name = derive_episode_name(rid)
    body_chars = len(text or "")
    out = {
        "rid": rid,
        "ep_name": ep_name,
        "doc_kind": doc_kind,
        "repo": repo,
        "body_chars": body_chars,
        "episode_added": False,
        "episode_reused": False,
        "facts_created": 0,
        "facts_skipped": 0,
        "facts_superseded": 0,
        "entities_resolved": 0,
        "entities_created": 0,
        "specdoc_uri": None,
        "session_uris": [],
        "error": None,
    }

    if not text:
        out["error"] = "empty_body"
        log_fn({"event": "skip_empty_body", **out})
        return out

    if dry_run:
        log_fn({"event": "dry_run", **out})
        return out

    # 1. Pre-resolve + persist SpecDoc entity for this ADR/foundation/architecture
    #    doc. Wave 2 B2: persist=True commits server-side via store_new_entity()
    #    so the URI is immediately referenceable in /knowledge/episodes facts.
    specdoc_label = specdoc_label_for(rid)
    try:
        specdoc_uri, specdoc_is_new, specdoc_persisted = resolve_entity_uri(
            client, specdoc_label, "SpecDoc", persist=True
        )
        out["specdoc_uri"] = specdoc_uri
        if specdoc_is_new:
            log_fn(
                {
                    "event": "specdoc_entity_created" if specdoc_persisted else "specdoc_entity_resolved_not_persisted",
                    "rid": rid,
                    "label": specdoc_label,
                    "uri": specdoc_uri,
                    "persisted": specdoc_persisted,
                }
            )
        else:
            log_fn(
                {
                    "event": "specdoc_entity_existed",
                    "rid": rid,
                    "uri": specdoc_uri,
                }
            )
    except Exception as e:
        out["error"] = f"specdoc_resolve_fail: {e}"
        log_fn({"event": "specdoc_resolve_fail", "rid": rid, "err": str(e)[:300]})
        return out

    # 2. For decision-record kinds: pre-resolve + persist Session entities and
    #    build AUTHORED_WITHIN facts.
    facts: list[dict] = []
    if doc_kind == "decision-record":
        m = re.match(
            r"doc-scanner:([^:]+):docs/research/canon-decisions/(\d{4})[a-z]?-",
            rid,
        )
        if m:
            parsed_repo, num_str = m.groups()
            session_uuids = sessions_for_adr(parsed_repo, int(num_str))
        else:
            session_uuids = [UNKNOWN_SESSION]

        for s_uuid in session_uuids:
            s_label = session_label_for(s_uuid)
            try:
                s_uri, s_is_new, s_persisted = resolve_entity_uri(
                    client, s_label, "Session", persist=True
                )
                out["session_uris"].append(s_uri)
                if s_is_new:
                    log_fn(
                        {
                            "event": "session_entity_created" if s_persisted else "session_entity_resolved_not_persisted",
                            "session_uuid": s_uuid,
                            "uri": s_uri,
                            "persisted": s_persisted,
                        }
                    )
            except Exception as e:
                log_fn(
                    {
                        "event": "session_resolve_fail",
                        "session_uuid": s_uuid,
                        "err": str(e)[:300],
                    }
                )
                continue

            facts.append(
                {
                    "subject": specdoc_label,
                    "predicate": "AUTHORED_WITHIN",
                    "object": s_label,
                    "fact_text": (
                        f"ADR {ep_name} authored within Claude Code session {s_uuid}"
                    ),
                    "valid_from": ref_time.isoformat(),
                    "valid_to": None,
                }
            )

    # 3. POST /knowledge/episodes
    payload = {
        "name": ep_name,
        "content": text[:8000] if text else None,  # truncate for storage hygiene
        "source_description": f"KOI rid={rid}",
        "source_document": rid,
        "group_id": GROUP_ID,
        "valid_at": ref_time.isoformat(),
        "metadata": {
            "batch_id": batch_id,
            "doc_kind": doc_kind,
            "repo": repo,
            "title": title,
        },
        "facts": facts,
        "create_entities": True,
    }
    resp = post_episode(client, payload, log_fn)
    if resp is None:
        out["error"] = "episode_post_failed"
        return out
    out["episode_added"] = not resp.get("episode_reused", False)
    out["episode_reused"] = bool(resp.get("episode_reused", False))
    out["facts_created"] = int(resp.get("facts_created", 0))
    out["facts_skipped"] = int(resp.get("facts_skipped", 0))
    out["facts_superseded"] = int(resp.get("facts_superseded", 0))
    out["entities_resolved"] = int(resp.get("entities_resolved", 0))
    out["entities_created"] = int(resp.get("entities_created", 0))
    out["episode_id"] = resp.get("episode_id")

    log_fn(
        {
            "event": "episode_ok",
            "rid": rid,
            "ep_name": ep_name,
            "episode_id": out["episode_id"],
            "episode_reused": out["episode_reused"],
            "facts_created": out["facts_created"],
            "facts_skipped": out["facts_skipped"],
            "facts_superseded": out["facts_superseded"],
        }
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample-rids", type=str, default=None,
                        help="Comma-separated RIDs for explicit sample-gate.")
    parser.add_argument("--batch-id", type=str, default=DEFAULT_BATCH_ID,
                        help=f"Batch ID stamp (default: {DEFAULT_BATCH_ID}).")
    parser.add_argument("--doc-kinds", type=str, default=",".join(DEFAULT_DOC_KINDS),
                        help="Comma-separated doc_kind allowlist.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Audit only; no writes.")
    parser.add_argument(
        "--log-path",
        type=str,
        default="/Users/darrenzal/projects/spore/tmp/koi-graph-consolidation/sustained-write-log.jsonl",
        help="JSONL log path.",
    )
    args = parser.parse_args()

    rid_filter = None
    if args.sample_rids:
        rid_filter = [r.strip() for r in args.sample_rids.split(",") if r.strip()]
    doc_kinds = tuple(k.strip() for k in args.doc_kinds.split(",") if k.strip())

    log_path = Path(args.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("a")

    def log_fn(rec: dict) -> None:
        rec["t"] = datetime.now(timezone.utc).isoformat()
        rec["batch_id"] = args.batch_id
        log_f.write(json.dumps(rec, default=str) + "\n")
        log_f.flush()

    # Pre-flight (httpx client reused for all calls). Bearer the CLAIMS token on
    # every request so the gated /knowledge/episodes write authenticates (harmless
    # on the open read/health endpoints).
    _auth_headers = (
        {"Authorization": f"Bearer {KOI_CLAIMS_SERVICE_TOKEN}"}
        if KOI_CLAIMS_SERVICE_TOKEN else {}
    )
    client = httpx.Client(headers=_auth_headers)
    health = preflight_health(client)
    log_fn({"event": "preflight_ok", "health": health})
    print(f"preflight ok: {health.get('embedding_model')}@{health.get('embedding_dimension')}")

    rows = fetch_rows(rid_filter=rid_filter, doc_kinds=doc_kinds)
    print(f"pulled {len(rows)} rows; batch_id={args.batch_id} doc_kinds={doc_kinds}")
    log_fn({"event": "start", "n_rows": len(rows), "doc_kinds": list(doc_kinds),
            "rid_filter": rid_filter, "dry_run": args.dry_run})

    if args.dry_run:
        for r in rows:
            print(f"  [dry-run] {r[0]} ({r[4]}, {len(r[2] or '')}c)")
        log_fn({"event": "done", "dry_run": True, "n_rows": len(rows)})
        log_f.close()
        return 0

    # Wave 2 B2: direct DB conn no longer needed for entity_registry writes —
    # /entity/resolve?persist=true commits server-side via store_new_entity().
    n_episodes_added = 0
    n_episodes_reused = 0
    n_facts_created = 0
    n_facts_skipped = 0
    n_facts_superseded = 0
    n_errors = 0
    t_start = time.time()
    try:
        for i, row in enumerate(rows):
            out = ingest_one(client, row, args.batch_id, log_fn)
            if out["episode_added"]:
                n_episodes_added += 1
            if out["episode_reused"]:
                n_episodes_reused += 1
            n_facts_created += out["facts_created"]
            n_facts_skipped += out["facts_skipped"]
            n_facts_superseded += out["facts_superseded"]
            if out["error"]:
                n_errors += 1
            marker = (
                "ok"
                if out["episode_added"]
                else ("reused" if out["episode_reused"] else "FAIL")
            )
            print(
                f"[{i+1}/{len(rows)}] {marker} {out['ep_name']} "
                f"({out['doc_kind']}, {out['body_chars']}c) "
                f"facts+{out['facts_created']}/-{out['facts_skipped']} "
                f"sup={out['facts_superseded']}"
            )
    finally:
        client.close()

    dur = time.time() - t_start
    summary = {
        "event": "done",
        "n_rows": len(rows),
        "n_episodes_added": n_episodes_added,
        "n_episodes_reused": n_episodes_reused,
        "n_facts_created": n_facts_created,
        "n_facts_skipped": n_facts_skipped,
        "n_facts_superseded": n_facts_superseded,
        "n_errors": n_errors,
        "dur_s": round(dur, 1),
        "group_id": GROUP_ID,
        "batch_id": args.batch_id,
    }
    log_fn(summary)
    log_f.close()

    print(
        f"\nDONE. episodes_added={n_episodes_added} reused={n_episodes_reused} "
        f"facts_created={n_facts_created} facts_skipped={n_facts_skipped} "
        f"facts_superseded={n_facts_superseded} errors={n_errors}"
    )
    print(f"     dur={dur:.0f}s batch_id={args.batch_id}")
    return 0 if n_errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
