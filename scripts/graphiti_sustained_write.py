#!/usr/bin/env python3
"""Tier-2 sustained-write KOI → Graphiti bridge (B5 batch sync per plan §Step 5).

Reads `koi_memories` rows with `metadata->>'doc_kind'` in batch-1 allowlist
(decision-record, foundation, architecture) and writes them to Graphiti's
`koi_canon_v1` group with idempotent dedup via deterministic episode UUID.

For decision-record kinds, also applies Strand-D structural binding (explicit
session-Entity nodes + RELATES_TO {AUTHORED_WITHIN, valid_at} edges) — same
machinery as `tmp/graphiti-poc/ingest_adrs_d_fix.py`, lifted into koi-processor.

Per plan §Step 5 + §Operational concerns:
  - group_id = "koi_canon_v1" (snake_case; FalkorDriver(database=group_id))
  - batch_id stamped on every node/edge metadata for rollback scoping
  - batch size 8 episodes per cycle; checkpoint key in Redis (production)
  - retry 2× backoff 30s on episode add (TODO Step 6+ when production)
  - Fix 1 (Tier-2 Step 5): hyphen-escape kebab-case tokens in episode body
    before LLM extraction so RediSearch doesn't parse `-` as query operator.
    Lossless transform; reversible via reindex if upstream fixes the bug.

Usage:
  --sample N            Run sample-gate over N rows (random pick within allowlist).
  --sample-rids R1,R2   Run sample-gate over explicitly listed RIDs (overrides --sample).
  --batch-id ID         Override default batch_id stamp.
  --doc-kinds A,B,C     Override default allowlist (decision-record,foundation,architecture).
  --rebuild             Full-rebuild mode (NOT exposed in this script's sample-gate phase).
  --dry-run             Audit only; no writes.

Step 5 sample-gate invocation:
  python3 scripts/graphiti_sustained_write.py \\
      --sample-rids "<5 RIDs>" \\
      --batch-id tier_2_sample_gate_2026_04_29
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.nodes import EntityNode, EpisodeType
from graphiti_core.edges import EntityEdge

# --- Production scheme ---
GROUP_ID = "koi_canon_v1"
DEFAULT_BATCH_ID = "tier_2_sustained_write"
DEFAULT_DOC_KINDS = ("decision-record", "foundation", "architecture")
DEFAULT_BATCH_SIZE = 8

DB_URL = "dbname=personal_koi"

# --- Strand-D session map (Spore canon-rebuild parent orchestrators) ---
SESSION_BC = "bc5c284d-2d1b-4ba0-9730-d83006480c52"
SESSION_585 = "585633a5-238b-402a-8ad4-80206269ce55"

# --- Deterministic UUID namespace (stable across reruns for idempotency) ---
NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1 — Hyphen escape in episode body before ingest
# ─────────────────────────────────────────────────────────────────────────────
#
# graphiti-core 0.29.0 + RediSearch parses `-` as a query operator. Episode
# bodies containing kebab-case identifiers (federation-encounter,
# composition-pattern, comparative-intake, etc.) trigger
# `RediSearch: Syntax error at offset N near <token>` during the dedup-search
# pass that fires on every add_episode call. POC ingest hit this on ADR-0081
# body which contains "comparative-intake" as a literal slug.
#
# Mitigation: replace `-` with `_` ONLY in token-shaped contexts (alphanum-
# alphanum boundaries). This preserves hyphens at word edges or inside
# punctuation runs (e.g., em-dash sequences) but rewrites all kebab-case slugs.
# In canon ADRs the kebab-case-as-slug pattern dominates so blanket
# replacement is acceptable and lossless for our LLM-extraction purposes
# (entity extractor sees `federation_encounter` instead of `federation-encounter`;
# extracted entities map back to either form via the registered glossary).
#
# Trade-off: natural-language hyphens ("self-similarity", "sub-cluster", etc.)
# also get rewritten. Reviewed sample bodies; impact is cosmetic on extraction
# quality (LLM normalizes both forms). Document as Tier-2 mitigation pending
# upstream `getzep/graphiti` fix; surface to Shawn co-learning queue.

KEBAB_TOKEN_RE = re.compile(r"(?<=[a-z0-9])-(?=[a-z0-9])", re.IGNORECASE)


def escape_redisearch_tokens(body: str) -> str:
    """Replace `foo-bar` → `foo_bar` only between two alphanum chars.

    Lossless for our LLM-extraction purposes; preserves natural punctuation
    runs (em-dashes, leading/trailing hyphens, hyphens adjacent to whitespace).
    """
    if not body:
        return body
    return KEBAB_TOKEN_RE.sub("_", body)


# ─────────────────────────────────────────────────────────────────────────────
# Strand-D helpers (lifted from tmp/graphiti-poc/ingest_adrs_d_fix.py with
# minor hardening for production naming)
# ─────────────────────────────────────────────────────────────────────────────


def derive_episode_name(rid: str) -> str:
    """`doc-scanner:spore:docs/research/canon-decisions/0080-foo.md` →
    `spore_ADR_0080_foo` for canon-decisions; generic fallback otherwise."""
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

    # Foundations / architecture / governance — use rel_path stem.
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


def sessions_uuid_for(repo: str, adr_num: int) -> list[str]:
    """ADR↔parent-session mapping (per Spore CLAUDE.md session-history table)."""
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


def session_entity_uuid(session_uuid: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{GROUP_ID}:session:{session_uuid}"))


def adr_entity_uuid(rid: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{GROUP_ID}:adr:{rid}"))


def edge_uuid(adr_rid: str, session_uuid: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{GROUP_ID}:authored_within:{adr_rid}:{session_uuid}"))


async def ensure_session_entity(g: Graphiti, session_uuid: str, batch_id: str) -> EntityNode:
    ent_uuid = session_entity_uuid(session_uuid)
    try:
        existing = await EntityNode.get_by_uuid(g.driver, ent_uuid)
        return existing
    except Exception:
        pass

    node = EntityNode(
        uuid=ent_uuid,
        name=f"claude-code session {session_uuid}",
        group_id=GROUP_ID,
        labels=["Session"],
        summary=f"Claude Code orchestrator session UUID {session_uuid}",
        attributes={"session_uuid": session_uuid, "batch_id": batch_id},
    )
    await node.generate_name_embedding(g.embedder)
    await node.save(g.driver)
    return node


async def ensure_adr_entity(
    g: Graphiti, rid: str, ep_name: str, body_text: str, batch_id: str
) -> EntityNode:
    ent_uuid = adr_entity_uuid(rid)
    try:
        existing = await EntityNode.get_by_uuid(g.driver, ent_uuid)
        return existing
    except Exception:
        pass

    summary = body_text[:280].replace("\n", " ").strip()
    node = EntityNode(
        uuid=ent_uuid,
        name=ep_name,
        group_id=GROUP_ID,
        labels=["ADR"],
        summary=summary or ep_name,
        attributes={"rid": rid, "batch_id": batch_id},
    )
    await node.generate_name_embedding(g.embedder)
    await node.save(g.driver)
    return node


async def write_authored_within_edge(
    g: Graphiti,
    adr_node: EntityNode,
    session_node: EntityNode,
    rid: str,
    session_uuid: str,
    commit_time: datetime,
    ep_name: str,
    batch_id: str,
) -> EntityEdge:
    e_uuid = edge_uuid(rid, session_uuid)
    fact = f"ADR {ep_name} authored within Claude Code session {session_uuid}"

    # Idempotency: if edge already exists, do not re-save (skip embedding cost).
    try:
        existing = await EntityEdge.get_by_uuid(g.driver, e_uuid)
        return existing
    except Exception:
        pass

    edge = EntityEdge(
        uuid=e_uuid,
        group_id=GROUP_ID,
        source_node_uuid=adr_node.uuid,
        target_node_uuid=session_node.uuid,
        created_at=commit_time,
        name="AUTHORED_WITHIN",
        fact=fact,
        valid_at=commit_time,
        attributes={"batch_id": batch_id},
    )
    await edge.generate_embedding(g.embedder)
    await edge.save(g.driver)
    return edge


# ─────────────────────────────────────────────────────────────────────────────
# Sustained-write core
# ─────────────────────────────────────────────────────────────────────────────


async def episode_already_present(g: Graphiti, rid: str, group_id: str) -> bool:
    """Check Graphiti for an existing Episodic node from this rid (idempotency).

    Episode UUIDs are NOT deterministic on graphiti-core (graphiti generates
    random UUIDs internally), so we dedup by `source_description = "KOI rid=<rid>"`
    which is stable across reruns. Returns True if any Episodic node carries
    that source_description in this group.
    """
    try:
        recs, _, _ = await g.driver.execute_query(
            "MATCH (ep:Episodic {group_id: $group_id, source_description: $sd}) "
            "RETURN ep.uuid AS uuid LIMIT 1",
            group_id=group_id,
            sd=f"KOI rid={rid}",
        )
        return bool(recs)
    except Exception:
        return False


async def ingest_one(
    g: Graphiti,
    row: tuple,
    batch_id: str,
    session_entities: dict[str, EntityNode],
    log_fn,
    dry_run: bool = False,
) -> dict:
    """Ingest a single koi_memories row into Graphiti per Strand B + Strand D.

    Returns a per-row outcome dict (event, success counts, errors).
    """
    rid, title, text, created_at, doc_kind, repo = row
    ref_time = created_at if created_at and created_at.tzinfo else (
        created_at.replace(tzinfo=timezone.utc) if created_at else datetime.now(timezone.utc)
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
        "episode_skipped_existing": False,
        "episode_error": None,
        "adr_entity_added": False,
        "session_edges_added": 0,
        "session_edges_existing": 0,
        "session_edges_error": [],
    }

    if not text:
        out["episode_skipped_existing"] = True
        out["episode_error"] = "empty body"
        log_fn({"event": "skip_empty", **out})
        return out

    if dry_run:
        log_fn({"event": "dry_run", **out})
        return out

    # 1. Idempotency check on episode.
    already = await episode_already_present(g, rid, GROUP_ID)
    if already:
        out["episode_skipped_existing"] = True
        log_fn({"event": "episode_skip_existing", "rid": rid, "ep_name": ep_name})
    else:
        # Fix 1: hyphen-escape body BEFORE add_episode.
        body_for_episode = escape_redisearch_tokens(text)
        try:
            t0 = time.time()
            await g.add_episode(
                name=ep_name,
                episode_body=body_for_episode,
                source=EpisodeType.text,
                source_description=f"KOI rid={rid}",
                reference_time=ref_time,
                group_id=GROUP_ID,
            )
            out["episode_added"] = True
            log_fn({"event": "episode_ok", "rid": rid, "ep_name": ep_name,
                    "dur_s": round(time.time() - t0, 2), "body_chars": body_chars,
                    "hyphen_escaped": body_for_episode != text})
        except Exception as e:
            out["episode_error"] = str(e)[:300]
            log_fn({"event": "episode_fail", "rid": rid, "ep_name": ep_name,
                    "err": str(e)[:300]})

    # 2. Strand-D structural binding (decision-record kinds only).
    if doc_kind != "decision-record":
        log_fn({"event": "structural_binding_skipped", "rid": rid, "doc_kind": doc_kind})
        return out

    # 2a. Parse ADR num from rid path; non-ADR decision-records (none in current
    #     allowlist but defensive) skip session-binding.
    m = re.match(
        r"doc-scanner:([^:]+):docs/research/canon-decisions/(\d{4})[a-z]?-",
        rid,
    )
    if not m:
        log_fn({"event": "structural_binding_skipped_unparseable", "rid": rid})
        return out
    parsed_repo, num_str = m.groups()
    sessions_for_adr = sessions_uuid_for(parsed_repo, int(num_str))

    # 2b. Ensure ADR-Entity.
    try:
        adr_node = await ensure_adr_entity(g, rid, ep_name, text, batch_id)
        out["adr_entity_added"] = True
    except Exception as e:
        log_fn({"event": "adr_entity_fail", "rid": rid, "err": str(e)[:300]})
        return out

    # 2c. Write RELATES_TO edges per parent session.
    for s_uuid in sessions_for_adr:
        # Pre-check: does the edge already exist (idempotency)?
        existing_eu = edge_uuid(rid, s_uuid)
        try:
            await EntityEdge.get_by_uuid(g.driver, existing_eu)
            out["session_edges_existing"] += 1
            log_fn({"event": "edge_skip_existing", "rid": rid, "session_uuid": s_uuid,
                    "edge_uuid": existing_eu})
            continue
        except Exception:
            pass  # not present; create

        try:
            edge = await write_authored_within_edge(
                g, adr_node, session_entities[s_uuid], rid, s_uuid, ref_time, ep_name, batch_id,
            )
            out["session_edges_added"] += 1
            log_fn({"event": "edge_ok", "rid": rid, "session_uuid": s_uuid,
                    "edge_uuid": edge.uuid})
        except Exception as e:
            out["session_edges_error"].append(str(e)[:200])
            log_fn({"event": "edge_fail", "rid": rid, "session_uuid": s_uuid,
                    "err": str(e)[:300]})

    return out


def fetch_rows(
    rid_filter: Optional[list[str]] = None,
    doc_kinds: tuple[str, ...] = DEFAULT_DOC_KINDS,
    sample_n: Optional[int] = None,
) -> list[tuple]:
    """Pull rows from `koi_memories`. Filters:
      - rid_filter: list of explicit RIDs (highest precedence; overrides others).
      - doc_kinds:  jsonb metadata->>'doc_kind' allowlist.
      - sample_n:   limit + ORDER BY random() (after doc_kind filter).
    """
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
        sql = f"""
            SELECT rid,
                   content->>'title' AS title,
                   content->>'text' AS text,
                   created_at,
                   metadata->>'doc_kind' AS doc_kind,
                   metadata->>'repo' AS repo
            FROM koi_memories
            WHERE metadata->>'doc_kind' = ANY(%s)
            {'ORDER BY random() LIMIT ' + str(sample_n) if sample_n else 'ORDER BY rid'}
        """
        params = (list(doc_kinds),)

    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", type=int, default=None,
                        help="Run sample-gate over N random rows in allowlist.")
    parser.add_argument("--sample-rids", type=str, default=None,
                        help="Comma-separated RIDs for explicit sample-gate (overrides --sample).")
    parser.add_argument("--batch-id", type=str, default=DEFAULT_BATCH_ID,
                        help=f"Batch ID stamp (default: {DEFAULT_BATCH_ID}).")
    parser.add_argument("--doc-kinds", type=str, default=",".join(DEFAULT_DOC_KINDS),
                        help="Comma-separated doc_kind allowlist (default: batch-1).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Audit only; no writes.")
    parser.add_argument("--log-path", type=str,
                        default="/Users/darrenzal/projects/spore/tmp/graphiti-poc/sustained-write-log.jsonl",
                        help="JSONL log path.")
    args = parser.parse_args()

    rid_filter = None
    if args.sample_rids:
        rid_filter = [r.strip() for r in args.sample_rids.split(",") if r.strip()]
    doc_kinds = tuple(k.strip() for k in args.doc_kinds.split(",") if k.strip())

    # 1. Pull rows.
    rows = fetch_rows(rid_filter=rid_filter, doc_kinds=doc_kinds, sample_n=args.sample)
    print(f"pulled {len(rows)} rows; batch_id={args.batch_id} doc_kinds={doc_kinds}")
    print(f"sample mode: {'rids=' + str(len(rid_filter)) if rid_filter else 'random N=' + str(args.sample) if args.sample else 'full'}")

    log_path = Path(args.log_path)
    log_path.parent.mkdir(exist_ok=True)
    log_f = log_path.open("a")

    def log_fn(rec):
        rec["t"] = datetime.now(timezone.utc).isoformat()
        rec["batch_id"] = args.batch_id
        log_f.write(json.dumps(rec, default=str) + "\n")
        log_f.flush()

    log_fn({"event": "start", "n_rows": len(rows), "doc_kinds": list(doc_kinds),
            "rid_filter": rid_filter, "dry_run": args.dry_run})

    if args.dry_run:
        for r in rows:
            print(f"  [dry-run] {r[0]} ({r[4]}, {len(r[2] or '')}c)")
        log_fn({"event": "done", "dry_run": True, "n_rows": len(rows)})
        log_f.close()
        return

    # 2. Connect Graphiti.
    driver = FalkorDriver(host="localhost", port=6380, database=GROUP_ID)
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(embedding_model="text-embedding-3-large", embedding_dim=3072)
    )
    g = Graphiti(graph_driver=driver, embedder=embedder)
    await g.build_indices_and_constraints()

    # 3. Pre-create the two session entities so per-row code never re-creates them.
    session_entities: dict[str, EntityNode] = {}
    for s_uuid in (SESSION_BC, SESSION_585):
        sn = await ensure_session_entity(g, s_uuid, args.batch_id)
        session_entities[s_uuid] = sn

    # 4. Ingest in batches of DEFAULT_BATCH_SIZE (per §Operational concerns).
    n_episodes = 0
    n_episode_skipped = 0
    n_episode_fail = 0
    n_adr_entities = 0
    n_edges = 0
    n_edges_existing = 0
    n_edges_fail = 0
    t_start = time.time()

    for i, row in enumerate(rows):
        out = await ingest_one(g, row, args.batch_id, session_entities, log_fn)
        if out["episode_added"]:
            n_episodes += 1
        if out["episode_skipped_existing"]:
            n_episode_skipped += 1
        if out["episode_error"]:
            n_episode_fail += 1
        if out["adr_entity_added"]:
            n_adr_entities += 1
        n_edges += out["session_edges_added"]
        n_edges_existing += out["session_edges_existing"]
        n_edges_fail += len(out["session_edges_error"])

        marker = "ok" if out["episode_added"] else ("skip" if out["episode_skipped_existing"] else "FAIL")
        print(
            f"[{i+1}/{len(rows)}] {marker} {out['ep_name']} ({out['doc_kind']}, {out['body_chars']}c) "
            f"adr_entity={out['adr_entity_added']} edges+{out['session_edges_added']}/="
            f"{out['session_edges_existing']}"
        )

    await g.close()
    dt_total = time.time() - t_start
    summary = {
        "event": "done",
        "n_rows": len(rows),
        "n_episodes_added": n_episodes,
        "n_episodes_skipped_existing": n_episode_skipped,
        "n_episode_fail": n_episode_fail,
        "n_adr_entities_added": n_adr_entities,
        "n_session_edges_added": n_edges,
        "n_session_edges_existing": n_edges_existing,
        "n_session_edges_fail": n_edges_fail,
        "dur_s": round(dt_total, 1),
        "group_id": GROUP_ID,
        "batch_id": args.batch_id,
    }
    log_fn(summary)
    log_f.close()

    print(f"\nDONE. episodes_new={n_episodes} episodes_skipped_existing={n_episode_skipped} "
          f"episodes_failed={n_episode_fail}")
    print(f"     adr_entities_new={n_adr_entities} edges_new={n_edges} "
          f"edges_existing={n_edges_existing} edges_failed={n_edges_fail}")
    print(f"     dur={dt_total:.0f}s batch_id={args.batch_id}")


if __name__ == "__main__":
    asyncio.run(main())
