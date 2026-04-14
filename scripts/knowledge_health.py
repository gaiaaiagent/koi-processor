#!/usr/bin/env python3
"""
Knowledge Health Report Generator

Checks completeness, coherence, and drift of governed docs in KOI.
Writes a report to the Obsidian vault at Inbox/YYYY-MM-DD Knowledge Health.md

Usage:
    cd /path/to/koi-processor
    POSTGRES_URL=... /path/to/venv/python3 scripts/knowledge_health.py [--repo REPO] [--repo-path PATH] [--dry-run]

Options:
    --repo REPO    Restrict to a specific repo (default: all scanned repos)
    --repo-path    Repo root path for disk-vs-KOI completeness checks
    --dry-run      Print report to stdout instead of writing to vault
    --days DAYS    Days threshold for stale facts (default: 90)
"""

import argparse
import asyncio
import json as jsonlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import asyncpg
import yaml

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
VAULT_PATH = Path(os.getenv("OBSIDIAN_VAULT_PATH", os.path.expanduser("~/Documents/Notes")))

STALE_DAYS_DEFAULT = 90
EXCLUDE_DIRS = {"node_modules", "venv", ".venv", "__pycache__", ".git", "dist", "build", "archive"}


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Extract YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}, content

    try:
        end = content.index("\n---\n", 3)
        raw_yaml = content[3:end].strip()
        body = content[end + 5:].strip()
        data = yaml.safe_load(raw_yaml) or {}
        if not isinstance(data, dict):
            return {}, content
        return data, body
    except (ValueError, yaml.YAMLError):
        return {}, content


def resolve_repo_path(repo_filter: str | None, repo_path_arg: str | None) -> Path | None:
    """Resolve a repo path for disk-vs-KOI completeness checks."""
    if repo_path_arg:
        path = Path(repo_path_arg).expanduser().resolve()
        return path if path.is_dir() else None

    if not repo_filter:
        return None

    repo_name = repo_filter.split("/")[-1]
    candidates = [
        Path.home() / "projects" / repo_name,
        Path.home() / "projects" / "RegenAI" / repo_name,
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


def discover_known_projects(projects_root: Path | None = None) -> Dict[str, Dict[str, str]]:
    """Return known local projects keyed by project_id."""
    root = (projects_root or (Path.home() / "projects")).expanduser()
    if not root.is_dir():
        return {}

    known: Dict[str, Dict[str, str]] = {}
    for project_json in root.rglob("project.json"):
        if project_json.parts[-3:] != ("docs", "_meta", "project.json"):
            continue
        try:
            payload = jsonlib.loads(project_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        project_id = payload.get("project_id")
        if not project_id:
            continue
        known[project_id] = {
            "project_id": project_id,
            "project_name": payload.get("project_name", project_id),
            "repo_root": str(project_json.parents[2]),
        }
    return known


def load_project_config(repo_path: Path | None) -> Dict[str, Any]:
    """Load docs/_meta/project.json for a repo if it exists."""
    if not repo_path:
        return {}
    config_path = repo_path / "docs" / "_meta" / "project.json"
    if not config_path.is_file():
        return {}
    try:
        payload = jsonlib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def scan_governed_docs_on_disk(repo_path: Path) -> Dict[str, Dict[str, str]]:
    """Return governed docs on disk keyed by doc_id."""
    docs: Dict[str, Dict[str, str]] = {}
    for path in sorted(repo_path.rglob("*.md")):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        frontmatter, _ = parse_frontmatter(raw)
        doc_id = frontmatter.get("doc_id")
        if not doc_id:
            continue
        docs[doc_id] = {
            "doc_id": doc_id,
            "doc_kind": frontmatter.get("doc_kind", ""),
            "rel_path": str(path.relative_to(repo_path)),
        }
    return docs


def scan_known_projects_on_disk(known_projects: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    """Return governed docs on disk for every known local project keyed by project_id."""
    repo_docs: Dict[str, Dict[str, Any]] = {}
    for project_id, info in sorted(known_projects.items()):
        repo_root = Path(info["repo_root"])
        if not repo_root.is_dir():
            continue
        docs = scan_governed_docs_on_disk(repo_root)
        repo_docs[project_id] = {
            "project_id": project_id,
            "project_name": info.get("project_name", project_id),
            "repo_root": str(repo_root),
            "docs": docs,
        }
    return repo_docs


async def run_health_checks(
    pool: asyncpg.Pool,
    repo_filter: str | None,
    stale_days: int,
    repo_path_arg: str | None,
) -> dict:
    """Run all health checks and return results dict."""
    results: dict = {}
    repo_path = resolve_repo_path(repo_filter, repo_path_arg)
    results["repo_path"] = str(repo_path) if repo_path else None
    project_config = load_project_config(repo_path)
    known_projects = discover_known_projects()
    results["known_projects"] = known_projects
    results["current_project_id"] = project_config.get("project_id")

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
        all_doc_id_rows = await conn.fetch("""
            SELECT DISTINCT metadata->>'doc_id' AS doc_id
            FROM koi_memories
            WHERE source_sensor = 'doc-scanner'
              AND metadata->>'doc_id' IS NOT NULL
        """)
        all_indexed_doc_ids = {r["doc_id"] for r in all_doc_id_rows}

        results["indexed_count"] = len(indexed_docs)
        results["indexed_docs"] = indexed_docs

        # 1a.1 Docs present on disk but missing from KOI (repo-local completeness)
        if repo_path:
            disk_docs = scan_governed_docs_on_disk(repo_path)
            missing_from_koi = [
                doc for doc_id, doc in disk_docs.items() if doc_id not in indexed_docs
            ]
            results["governed_docs_on_disk_count"] = len(disk_docs)
            results["missing_from_koi"] = missing_from_koi
            results["missing_from_koi_by_repo"] = None
        else:
            known_repo_docs = scan_known_projects_on_disk(known_projects)
            missing_from_koi_by_repo: Dict[str, Dict[str, Any]] = {}
            total_docs = 0
            total_missing = 0
            for project_id, repo_info in known_repo_docs.items():
                docs = repo_info["docs"]
                total_docs += len(docs)
                missing_docs = [
                    doc for doc_id, doc in docs.items()
                    if doc_id not in all_indexed_doc_ids
                ]
                total_missing += len(missing_docs)
                missing_from_koi_by_repo[project_id] = {
                    "project_name": repo_info["project_name"],
                    "repo_root": repo_info["repo_root"],
                    "total_docs": len(docs),
                    "missing_docs": missing_docs,
                }
            results["governed_docs_on_disk_count"] = total_docs
            results["missing_from_koi"] = []
            results["missing_from_koi_by_repo"] = missing_from_koi_by_repo
            results["missing_from_koi_total"] = total_missing

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

        # 1c. Missing depends_on references (classified by local-vs-external scope)
        broken_deps = []
        external_deps = []
        for doc_id, doc in indexed_docs.items():
            depends_raw = doc.get("depends_on")
            deps = json.loads(depends_raw) if depends_raw else []
            for dep in deps:
                if not dep or not isinstance(dep, str):
                    continue
                if dep not in all_indexed_doc_ids:
                    dep_prefix = dep.split(".", 1)[0] if "." in dep else ""
                    dep_info = {
                        "doc_id": doc_id,
                        "missing_dep": dep,
                        "missing_dep_prefix": dep_prefix,
                        "repo": doc.get("repo"),
                    }
                    if dep_prefix and dep_prefix in known_projects:
                        broken_deps.append(dep_info)
                    else:
                        external_deps.append(dep_info)
        results["broken_deps"] = broken_deps
        results["external_deps"] = external_deps

        # ── 2. Coherence ────────────────────────────────────────────────────

        # 2a. Stale facts (active facts older than stale_days)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        stale_total = await conn.fetchval("""
            SELECT COUNT(*) FROM knowledge_facts
            WHERE valid_to IS NULL AND created_at < $1
        """, stale_cutoff)
        results["stale_facts_count"] = stale_total or 0
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

        # Get total fact count for context
        total_facts = await conn.fetchval(
            "SELECT COUNT(*) FROM knowledge_facts WHERE valid_to IS NULL")
        results["total_facts"] = total_facts

        # 2b. Orphaned entities (zero document_entity_links)
        orphan_total = await conn.fetchval("""
            SELECT COUNT(*)
            FROM entity_registry e
            LEFT JOIN document_entity_links d ON d.entity_uri = e.fuseki_uri
            WHERE d.entity_uri IS NULL
              AND NOT e.node_private
        """)
        orphan_rows = await conn.fetch("""
            SELECT e.fuseki_uri, e.entity_text, e.entity_type
            FROM entity_registry e
            LEFT JOIN document_entity_links d ON d.entity_uri = e.fuseki_uri
            WHERE d.entity_uri IS NULL
              AND NOT e.node_private
            LIMIT 20
        """)
        results["orphaned_entities"] = [dict(r) for r in orphan_rows]
        results["orphaned_entity_count"] = orphan_total or 0

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
        results["total_chunks"] = (chunk_stats["total_chunks"] or 0) if chunk_stats else 0
        results["missing_embeddings"] = (chunk_stats["missing_embeddings"] or 0) if chunk_stats else 0

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
    missing_from_koi = results["missing_from_koi"]
    missing_from_koi_by_repo = results.get("missing_from_koi_by_repo")
    missing_from_koi_total = (
        results.get("missing_from_koi_total")
        if missing_from_koi_by_repo is not None
        else (len(missing_from_koi) if missing_from_koi is not None else None)
    )
    missing_deps = len(results["missing_depends_on"])
    broken = len(results["broken_deps"])
    external = len(results["external_deps"])
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
        f"| Missing from KOI | {missing_from_koi_total if missing_from_koi_total is not None else 'n/a'} |",
        f"| Missing depends_on | {missing_deps} |",
        f"| Broken local depends_on refs | {broken} |",
        f"| External depends_on refs | {external} |",
        f"| Stale facts (>{stale_days}d) | {stale_facts} / {total_facts} total |",
        f"| Orphaned entities | {orphans} / {total_entities} total |",
        f"| Missing embeddings | {missing_emb} / {total_chunks} chunks |",
        f"| Authority collisions | {collisions} |",
        "",
    ]

    # Repos scanned
    lines += ["## Repos Scanned", ""]
    if results["repos_scanned"]:
        for r in results["repos_scanned"]:
            lines.append(f"- **{r['repo']}**: {r['doc_count']} governed docs indexed")
    else:
        lines.append("_No repos indexed yet. Run doc_scanner.py to index governed docs._")
    lines.append("")

    # Missing from KOI
    lines += ["## Missing from KOI", ""]
    if missing_from_koi_by_repo is not None:
        repo_items = list(missing_from_koi_by_repo.items())
        repo_missing_total = sum(len(item["missing_docs"]) for _, item in repo_items)
        if repo_missing_total:
            lines.append(f"Governed docs present on disk but not indexed in KOI — {repo_missing_total} found:")
            lines.append("")
            for project_id, item in repo_items:
                missing_docs = item["missing_docs"]
                if missing_docs:
                    lines.append(
                        f"- **{project_id}** ({item['project_name']}): {len(missing_docs)} missing / {item['total_docs']} on disk"
                    )
                    for doc in missing_docs[:10]:
                        lines.append(f"  - `{doc['doc_id']}` ({doc['doc_kind']}) at `{doc['rel_path']}`")
            lines.append("")
        else:
            lines.append("_None — all governed docs across known local projects are indexed in KOI._")
            lines.append("")
    elif missing_from_koi is None:
        lines.append("_Disk-vs-KOI completeness check skipped — pass `--repo-path` or use a resolvable repo name._")
        lines.append("")
    elif missing_from_koi:
        lines.append(f"Governed docs present on disk but not indexed in KOI — {len(missing_from_koi)} found (first {min(20, len(missing_from_koi))} shown):")
        lines.append("")
        for item in missing_from_koi[:20]:
            lines.append(f"- `{item['doc_id']}` ({item['doc_kind']}) at `{item['rel_path']}`")
        lines.append("")
    else:
        lines.append("_None — all governed docs on disk are indexed in KOI._")
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
            "## Broken Local depends_on References",
            "",
            f"References to doc_ids in known local projects that are not yet in KOI — {broken} found:",
            "",
        ]
        for item in results["broken_deps"]:
            lines.append(f"- `{item['doc_id']}` → `{item['missing_dep']}` (not indexed)")
        lines.append("")
    else:
        lines += ["## Broken Local depends_on References", "", "_None — all local project dependencies are indexed._", ""]

    # External deps
    if external:
        lines += [
            "## External depends_on References",
            "",
            (
                "References to doc_ids whose project prefix is not currently registered as a local project "
                f"or indexed in KOI — {external} found:"
            ),
            "",
        ]
        for item in results["external_deps"]:
            prefix = item["missing_dep_prefix"] or "unknown-prefix"
            lines.append(
                f"- `{item['doc_id']}` → `{item['missing_dep']}` "
                f"(external/unregistered prefix `{prefix}`)"
            )
        lines.append("")
    else:
        lines += ["## External depends_on References", "", "_None — no unresolved external dependencies._", ""]

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
            f"Active facts older than {stale_days} days — {stale_facts} total (first {min(10, len(results['stale_facts']))} shown, of {total_facts} active):",
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
            f"Entities with zero document_entity_links — {orphans} total (first {min(10, len(results['orphaned_entities']))} shown, of {total_entities} entities):",
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
    if missing_from_koi_by_repo is not None:
        if missing_from_koi_total:
            actions.append(f"- Index {missing_from_koi_total} governed doc(s) missing from KOI across local projects")
    elif missing_from_koi:
        actions.append(f"- Index {len(missing_from_koi)} governed doc(s) missing from KOI")
    if broken:
        actions.append(f"- Index {broken} missing doc(s) referenced by depends_on")
    if external:
        actions.append(
            f"- Review {external} external depends_on reference(s) — either register/index the source project or explicitly accept them as external"
        )
    if missing_deps:
        actions.append(f"- Add depends_on to {missing_deps} non-root docs")
    if missing_emb:
        actions.append(f"- Re-run `doc_scanner.py --force` to fix {missing_emb} chunk(s) without embeddings")
    if stale_facts > 10:
        actions.append(f"- Review {stale_facts} stale facts — consider adding a knowledge episode to refresh context")
    if not actions:
        actions.append("- No critical actions needed — system is healthy.")
    lines += actions
    lines.append("")

    return "\n".join(lines)


async def main(repo_filter: str | None, dry_run: bool, stale_days: int, repo_path: str | None):
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        results = await run_health_checks(pool, repo_filter, stale_days, repo_path)
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
    parser.add_argument("--repo-path", help="Path to repo root for disk-vs-KOI completeness checks")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout only")
    parser.add_argument("--days", type=int, default=STALE_DAYS_DEFAULT,
                        help="Days threshold for stale facts")
    args = parser.parse_args()

    asyncio.run(main(args.repo, args.dry_run, args.days, args.repo_path))
