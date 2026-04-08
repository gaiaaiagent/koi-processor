#!/usr/bin/env python3
"""
Knowledge Health Report Generator

Checks completeness, coherence, and drift of governed docs in KOI.
Writes a report to the Obsidian vault at Inbox/YYYY-MM-DD Knowledge Health.md

Usage:
    cd /path/to/koi-processor
    POSTGRES_URL=... /path/to/venv/python3 scripts/knowledge_health.py [--repo REPO] [--dry-run]

Options:
    --repo REPO    Restrict to a specific repo (default: all scanned repos)
    --dry-run      Print report to stdout instead of writing to vault
    --days DAYS    Days threshold for stale facts (default: 90)
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
VAULT_PATH = Path(os.getenv("OBSIDIAN_VAULT_PATH", os.path.expanduser("~/Documents/Notes")))

STALE_DAYS_DEFAULT = 90


async def run_health_checks(pool: asyncpg.Pool, repo_filter: str | None, stale_days: int) -> dict:
    """Run all health checks and return results dict."""
    results: dict = {}

    async with pool.acquire() as conn:

        # ── 1. Completeness ──────────────────────────────────────────────────

        # 1a. All governed docs indexed (by source_sensor='doc-scanner')
        rows = await conn.fetch("""
            SELECT metadata->>'doc_id' AS doc_id,
                   metadata->>'doc_kind' AS doc_kind,
                   metadata->>'status' AS status,
                   metadata->>'repo' AS repo,
                   metadata->>'rel_path' AS rel_path,
                   metadata->>'depends_on' AS depends_on
            FROM koi_memories
            WHERE source_sensor = 'doc-scanner'
              AND metadata->>'doc_id' IS NOT NULL
        """ + (" AND metadata->>'repo' = $1" if repo_filter else ""),
        *([repo_filter] if repo_filter else []))

        indexed_docs = {r["doc_id"]: dict(r) for r in rows}

        results["indexed_count"] = len(indexed_docs)
        results["indexed_docs"] = indexed_docs

        # 1b. Docs with missing depends_on (non-root docs)
        # Root docs: depends_on is empty/null AND doc_kind in (vision, project)
        root_kinds = {"vision", "project"}
        missing_depends_on = []
        for doc_id, doc in indexed_docs.items():
            depends_raw = doc.get("depends_on")
            deps = json.loads(depends_raw) if depends_raw else []
            doc_kind = doc.get("doc_kind", "")
            if not deps and doc_kind not in root_kinds:
                missing_depends_on.append({
                    "doc_id": doc_id,
                    "doc_kind": doc_kind,
                    "repo": doc.get("repo"),
                })
        results["missing_depends_on"] = missing_depends_on

        # 1c. Broken depends_on references (points to doc_id not in KOI)
        broken_deps = []
        for doc_id, doc in indexed_docs.items():
            depends_raw = doc.get("depends_on")
            deps = json.loads(depends_raw) if depends_raw else []
            for dep in deps:
                if dep not in indexed_docs:
                    broken_deps.append({
                        "doc_id": doc_id,
                        "missing_dep": dep,
                        "repo": doc.get("repo"),
                    })
        results["broken_deps"] = broken_deps

        # ── 2. Coherence ────────────────────────────────────────────────────

        # 2a. Stale facts (valid_to IS NULL, created > stale_days ago, no recent episode)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        stale_rows = await conn.fetch("""
            SELECT f.id, f.fact_text, f.created_at, e.name AS episode_name
            FROM knowledge_facts f
            LEFT JOIN knowledge_episodes e ON f.episode_id = e.id
            WHERE f.valid_to IS NULL
              AND f.created_at < $1
            ORDER BY f.created_at ASC
            LIMIT 20
        """, stale_cutoff)
        results["stale_facts"] = [dict(r) for r in stale_rows]
        results["stale_facts_count"] = len(stale_rows)

        # Get total fact count for context
        total_facts = await conn.fetchval(
            "SELECT COUNT(*) FROM knowledge_facts WHERE valid_to IS NULL")
        results["total_facts"] = total_facts

        # 2b. Orphaned entities (zero document_entity_links)
        orphan_rows = await conn.fetch("""
            SELECT e.fuseki_uri, e.entity_text, e.entity_type
            FROM entity_registry e
            LEFT JOIN document_entity_links d ON d.entity_uri = e.fuseki_uri
            WHERE d.entity_uri IS NULL
              AND NOT e.node_private
            LIMIT 20
        """)
        results["orphaned_entities"] = [dict(r) for r in orphan_rows]
        results["orphaned_entity_count"] = len(orphan_rows)

        total_entities = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_registry WHERE NOT node_private")
        results["total_entities"] = total_entities

        # 2c. Authority collisions: same doc_id claimed by multiple repos
        collision_rows = await conn.fetch("""
            SELECT metadata->>'doc_id' AS doc_id,
                   array_agg(DISTINCT metadata->>'repo') AS repos,
                   COUNT(*) AS count
            FROM koi_memories
            WHERE source_sensor = 'doc-scanner'
              AND metadata->>'doc_id' IS NOT NULL
            GROUP BY metadata->>'doc_id'
            HAVING COUNT(DISTINCT metadata->>'repo') > 1
        """)
        results["authority_collisions"] = [dict(r) for r in collision_rows]

        # ── 3. Drift ────────────────────────────────────────────────────────

        # 3a. Docs with non-null embeddings vs total indexed
        chunk_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total_chunks,
                SUM(CASE WHEN mc.embedding IS NULL THEN 1 ELSE 0 END) AS missing_embeddings
            FROM koi_memory_chunks mc
            JOIN koi_memories m ON mc.document_rid = m.rid
            WHERE m.source_sensor = 'doc-scanner'
        """ + (" AND m.metadata->>'repo' = $1" if repo_filter else ""),
        *([repo_filter] if repo_filter else []))
        results["total_chunks"] = chunk_stats["total_chunks"] if chunk_stats else 0
        results["missing_embeddings"] = chunk_stats["missing_embeddings"] if chunk_stats else 0

        # 3b. Docs indexed (for comparing to known doc count)
        repos_scanned = await conn.fetch("""
            SELECT metadata->>'repo' AS repo, COUNT(*) AS doc_count
            FROM koi_memories
            WHERE source_sensor = 'doc-scanner'
            GROUP BY metadata->>'repo'
            ORDER BY metadata->>'repo'
        """)
        results["repos_scanned"] = [dict(r) for r in repos_scanned]

    return results


def render_report(results: dict, stale_days: int) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"# Knowledge Health Report — {today}",
        "",
        "Generated by `scripts/knowledge_health.py`.",
        "",
        "---",
        "",
        "## Summary",
        "",
    ]

    indexed = results["indexed_count"]
    missing_deps = len(results["missing_depends_on"])
    broken = len(results["broken_deps"])
    stale_facts = results["stale_facts_count"]
    total_facts = results["total_facts"]
    orphans = results["orphaned_entity_count"]
    total_entities = results["total_entities"]
    missing_emb = results["missing_embeddings"]
    total_chunks = results["total_chunks"]
    collisions = len(results["authority_collisions"])

    lines += [
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Governed docs indexed | {indexed} |",
        f"| Missing depends_on | {missing_deps} |",
        f"| Broken depends_on refs | {broken} |",
        f"| Stale facts (>{stale_days}d) | {stale_facts} / {total_facts} total |",
        f"| Orphaned entities | {orphans} / {total_entities} total |",
        f"| Missing embeddings | {missing_emb} / {total_chunks} chunks |",
        f"| Authority collisions | {collisions} |",
        "",
    ]

    # Repos scanned
    lines += ["## Repos Scanned", ""]
    for r in results["repos_scanned"]:
        lines.append(f"- **{r['repo']}**: {r['doc_count']} governed docs indexed")
    lines.append("")

    # Missing depends_on
    if missing_deps:
        lines += [
            "## Dependency Gaps",
            "",
            f"Non-root docs with empty `depends_on` — {missing_deps} found:",
            "",
        ]
        for item in results["missing_depends_on"]:
            lines.append(f"- `{item['doc_id']}` ({item['doc_kind']}) in `{item['repo']}`")
        lines.append("")
    else:
        lines += ["## Dependency Gaps", "", "_None — all non-root docs have depends_on set._", ""]

    # Broken deps
    if broken:
        lines += [
            "## Broken depends_on References",
            "",
            f"References to doc_ids not yet in KOI — {broken} found:",
            "",
        ]
        for item in results["broken_deps"]:
            lines.append(f"- `{item['doc_id']}` → `{item['missing_dep']}` (not indexed)")
        lines.append("")
    else:
        lines += ["## Broken depends_on References", "", "_None — all referenced doc_ids are indexed._", ""]

    # Authority collisions
    if collisions:
        lines += ["## Authority Concerns", "", "Same doc_id claimed by multiple repos:", ""]
        for c in results["authority_collisions"]:
            lines.append(f"- `{c['doc_id']}` found in: {', '.join(c['repos'])}")
        lines.append("")
    else:
        lines += ["## Authority Concerns", "", "_None — no doc_id collisions across repos._", ""]

    # Stale facts
    if stale_facts:
        lines += [
            "## Stale Facts",
            "",
            f"Facts older than {stale_days} days with no expiry — {stale_facts} shown (of {total_facts} total active):",
            "",
        ]
        for f in results["stale_facts"][:10]:
            age = (datetime.now(timezone.utc) - f["created_at"]).days
            ep = f["episode_name"] or "unknown episode"
            text = f["fact_text"][:100] if f["fact_text"] else "(empty)"
            lines.append(f"- [{age}d] {text} _(from {ep})_")
        lines.append("")
    else:
        lines += ["## Stale Facts", "", f"_None — no facts older than {stale_days} days._", ""]

    # Orphaned entities
    if orphans:
        lines += [
            "## Orphaned Entities",
            "",
            f"Entities with zero document_entity_links — {orphans} of {total_entities} shown:",
            "",
        ]
        for e in results["orphaned_entities"][:10]:
            lines.append(f"- `{e['entity_text']}` ({e['entity_type']})")
        lines.append("")
    else:
        lines += ["## Orphaned Entities", "", "_None — all entities have at least one document link._", ""]

    # Embedding status
    if missing_emb:
        lines += [
            "## Missing Embeddings",
            "",
            f"{missing_emb} of {total_chunks} doc chunks have no embedding vector — text-fallback only for these.",
            "",
        ]
    else:
        lines += ["## Missing Embeddings", "", f"_All {total_chunks} doc chunks are embedded._", ""]

    # Suggested actions
    lines += ["## Suggested Actions", ""]
    actions = []
    if broken:
        actions.append(f"- Index {broken} missing doc(s) referenced by depends_on")
    if missing_deps:
        actions.append(f"- Add depends_on to {missing_deps} non-root docs")
    if missing_emb:
        actions.append(f"- Re-run `doc_scanner.py --force` to fix {missing_emb} unchunked docs")
    if stale_facts > 10:
        actions.append(f"- Review {stale_facts} stale facts — consider adding a knowledge episode to refresh context")
    if not actions:
        actions.append("- No critical actions needed — system is healthy.")
    lines += actions
    lines.append("")

    return "\n".join(lines)


async def main(repo_filter: str | None, dry_run: bool, stale_days: int):
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        results = await run_health_checks(pool, repo_filter, stale_days)
        report = render_report(results, stale_days)
    finally:
        await pool.close()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note_path = f"Inbox/{today} Knowledge Health.md"

    if dry_run:
        print(report)
        print(f"\n[DRY-RUN] Would write to vault: {note_path}")
        return

    vault_file = VAULT_PATH / note_path
    vault_file.parent.mkdir(parents=True, exist_ok=True)
    vault_file.write_text(report, encoding="utf-8")
    print(f"Written: {vault_file}")
    print(f"Vault note: {note_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Restrict to a specific repo")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout only")
    parser.add_argument("--days", type=int, default=STALE_DAYS_DEFAULT,
                        help="Days threshold for stale facts")
    args = parser.parse_args()

    asyncio.run(main(args.repo, args.dry_run, args.days))
