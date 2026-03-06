#!/usr/bin/env python3
"""
Execution Flow Detection for Code Graph

Post-processing script that detects execution flows via entry point scoring
and BFS tracing over the Apache AGE code graph in PostgreSQL.

Pipeline:
1. Pull callable nodes (Function/Method/Handler) and CALLS edges
2. Score each node as a potential entry point (GitNexus heuristics)
3. BFS from top entry points following CALLS edges (max depth 10)
4. Deduplicate subset traces, keep longest per entry->terminal pair
5. Store Process nodes + STEP_IN_PROCESS edges in AGE

Usage:
    python scripts/detect_flows.py --repo koi-processor
    python scripts/detect_flows.py --all-repos
    python scripts/detect_flows.py --repo koi-processor --dry-run
    python scripts/detect_flows.py --repo koi-processor --top-entries 100 --max-processes 50
"""

import argparse
import asyncio
import hashlib
import os
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import asyncpg
from loguru import logger

# ---------------------------------------------------------------------------
# Database configuration
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": os.environ.get("KOI_DB_HOST", "localhost"),
    "port": int(os.environ.get("KOI_DB_PORT", "5433")),
    "database": os.environ.get("KOI_DB_NAME", "eliza"),
    "user": os.environ.get("KOI_DB_USER", "postgres"),
    "password": os.environ.get("KOI_DB_PASSWORD", "postgres"),
}
DEFAULT_GRAPH = "regen_graph"

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------
ENTRY_PATTERNS: Dict[str, List[str]] = {
    "*": [r"^(main|init|bootstrap|start|run|setup)$", r"^handle[A-Z]", r"^on[A-Z]",
          r"Handler$", r"Controller$", r"^process[A-Z]", r"^execute[A-Z]", r"^dispatch[A-Z]"],
    "python": [r"^(get|post|put|delete)_", r"^api_", r"^view_", r"^app$"],
    "typescript": [r"^use[A-Z]"],
    "go": [r"Handler$", r"^Serve", r"^New[A-Z]", r"^Make[A-Z]",
           r"^Msg", r"^Query", r"^BeginBlocker$", r"^EndBlocker$",
           r"^InitGenesis$", r"^ExportGenesis$",
           r"^Keeper\.", r"^NewKeeper$", r"^NewMsgServer", r"^NewQueryServer"],
}

UTILITY_PATTERNS: List[str] = [
    r"^(get|set|is|has|can|should)[A-Z]", r"^_",
    r"^(format|parse|validate|convert|transform)",
    r"^(log|debug|error|warn|info)$",
    r"^(to|from)[A-Z]", r"^(encode|decode)", r"Helper$", r"Util$",
]

FRAMEWORK_PATH_PATTERNS: Dict[str, Tuple[List[str], str, float]] = {
    "fastapi":       (["/routers/", "/endpoints/", "/api/"], ".py", 2.5),
    "express":       (["/routes/"], ".ts", 2.5),
    "go-http":       (["/handlers/", "/handler/"], ".go", 2.5),
    "cosmos-keeper": (["/keeper/"], ".go", 3.0),
    "cosmos-cli":    (["/cli/"], ".go", 2.5),
    "cosmos-module": (["/module/"], ".go", 2.0),
    "nextjs-pages":  (["/pages/"], ".tsx", 3.0),
    "nextjs-api":    (["/pages/api/", "/app/api/"], ".ts", 3.0),
}

TEST_PATTERNS: List[str] = [
    "/test/", "/tests/", ".test.", ".spec.", "_test.go", "_test.py",
    "test_", "__tests__/", "testing/",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def escape_cypher(text: str) -> str:
    """Escape special characters for Cypher string literals."""
    if not text:
        return ""
    return (text.replace("\\", "\\\\").replace("'", "\\'")
                .replace('"', '\\"').replace("\n", "\\n")
                .replace("\r", "\\r").replace("\t", "\\t"))

async def setup_age(conn: asyncpg.Connection) -> None:
    await conn.execute("LOAD 'age';")
    await conn.execute("SET search_path = ag_catalog, '$user', public;")

def is_test_file(file_path: str) -> bool:
    if not file_path:
        return False
    return any(p in file_path for p in TEST_PATTERNS)

# ---------------------------------------------------------------------------
# Label management
# ---------------------------------------------------------------------------
async def ensure_labels(conn: asyncpg.Connection, graph_name: str) -> None:
    """Create Process vertex label and STEP_IN_PROCESS edge label if missing."""
    graph_oid = await conn.fetchval(
        "SELECT graphid FROM ag_catalog.ag_graph WHERE name = $1", graph_name)
    if graph_oid is None:
        raise RuntimeError(f"Graph '{graph_name}' does not exist")

    for label_name, kind in [("Process", "vertex"), ("STEP_IN_PROCESS", "edge")]:
        exists = await conn.fetchval(
            "SELECT COUNT(*) FROM ag_catalog.ag_label WHERE name = $1 AND graph = $2",
            label_name, graph_oid)
        if exists == 0:
            if kind == "vertex":
                await conn.execute(f"""
                    SELECT * FROM cypher('{graph_name}', $$
                        CREATE (n:{label_name} {{_init: true}}) DELETE n
                    $$) as (result agtype);""")
            else:
                await conn.execute(f"""
                    SELECT * FROM cypher('{graph_name}', $$
                        CREATE (a:_Dummy)-[r:{label_name}]->(b:_Dummy) DELETE r, a, b
                    $$) as (result agtype);""")
            logger.info(f"Created {kind} label '{label_name}' in {graph_name}")

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
async def cleanup_old_flows(conn: asyncpg.Connection, repo: str, graph_name: str) -> int:
    """DETACH DELETE all Process nodes for *repo*. Returns count deleted."""
    row = await conn.fetchrow(f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (p:Process {{repo: '{escape_cypher(repo)}'}}) RETURN count(p)
        $$) as (cnt agtype);""")
    old_count = int(str(row["cnt"])) if row else 0
    if old_count > 0:
        await conn.execute(f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (p:Process {{repo: '{escape_cypher(repo)}'}}) DETACH DELETE p
            $$) as (result agtype);""")
        logger.info(f"Deleted {old_count} old Process nodes for {repo}")
    return old_count

# ---------------------------------------------------------------------------
# Pull call graph
# ---------------------------------------------------------------------------
async def pull_call_graph(
    conn: asyncpg.Connection, repo: str, graph_name: str,
) -> Tuple[Dict[str, dict], Dict[str, List[str]], Dict[str, List[str]]]:
    """Fetch callable nodes + CALLS edges. Returns (nodes, adj, rev_adj).

    Queries by both vertex label AND entity_type property since production
    graph (regen_graph) uses labels while staging (regen_graph_v2) uses properties.
    """
    CALLABLE_TYPES = ("Function", "Method", "Handler")
    repo_esc = escape_cypher(repo)

    # Query by vertex label (production graph pattern)
    all_rows = []
    for ntype in CALLABLE_TYPES:
        try:
            rows = await conn.fetch(f"""
                SELECT * FROM cypher('{graph_name}', $$
                    MATCH (n:{ntype})
                    WHERE n.repo = '{repo_esc}'
                    RETURN n.entity_id, n.name, '{ntype}' as entity_type,
                           n.file_path, n.language, n.line_start
                $$) as (entity_id agtype, name agtype, entity_type agtype,
                        file_path agtype, language agtype, line_start agtype);""")
            all_rows.extend(rows)
        except Exception:
            pass  # Label may not exist

    # Also query by entity_type property (staging graph pattern)
    try:
        prop_rows = await conn.fetch(f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (n)
                WHERE n.repo = '{repo_esc}'
                  AND (n.entity_type = 'Function' OR n.entity_type = 'Method'
                       OR n.entity_type = 'Handler')
                RETURN n.entity_id, n.name, n.entity_type, n.file_path, n.language, n.line_start
            $$) as (entity_id agtype, name agtype, entity_type agtype,
                    file_path agtype, language agtype, line_start agtype);""")
        all_rows.extend(prop_rows)
    except Exception:
        pass

    nodes: Dict[str, dict] = {}
    for row in all_rows:
        eid = str(row["entity_id"]).strip('"')
        if eid in nodes:
            continue  # deduplicate
        nodes[eid] = {
            "entity_id": eid,
            "name": str(row["name"]).strip('"'),
            "entity_type": str(row["entity_type"]).strip('"'),
            "file_path": str(row["file_path"]).strip('"'),
            "language": str(row["language"]).strip('"'),
            "line_start": int(str(row["line_start"])) if row["line_start"] else 0,
        }
    logger.info(f"Pulled {len(nodes)} callable nodes for {repo}")

    edge_rows = await conn.fetch(f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (a)-[:CALLS]->(b) WHERE a.repo = '{escape_cypher(repo)}'
            RETURN a.entity_id, b.entity_id
        $$) as (source agtype, target agtype);""")

    adj: Dict[str, List[str]] = defaultdict(list)
    rev_adj: Dict[str, List[str]] = defaultdict(list)
    for row in edge_rows:
        src = str(row["source"]).strip('"')
        tgt = str(row["target"]).strip('"')
        if src in nodes and tgt in nodes:
            adj[src].append(tgt)
            rev_adj[tgt].append(src)

    total_edges = sum(len(v) for v in adj.values())
    logger.info(f"Pulled {total_edges} CALLS edges for {repo}")
    return nodes, dict(adj), dict(rev_adj)

# ---------------------------------------------------------------------------
# Entry point scoring
# ---------------------------------------------------------------------------
def _matches_any(name: str, patterns: List[str]) -> bool:
    return any(re.search(pat, name) for pat in patterns)

def score_entry_point(node: dict, callee_count: int, caller_count: int) -> float:
    """Score = (callee_count / (caller_count + 1)) * export * name * framework."""
    name = node["name"]
    language = node.get("language", "")
    file_path = node.get("file_path", "")

    base = callee_count / (caller_count + 1)

    # Export multiplier
    export_mult = 1.0
    if language == "go" and name and name[0].isupper():
        export_mult = 2.0
    elif "export" in node.get("entity_type", "").lower():
        export_mult = 2.0

    # Name multiplier
    name_mult = 1.0
    lang_pats = ENTRY_PATTERNS.get(language, [])
    wild_pats = ENTRY_PATTERNS.get("*", [])
    if _matches_any(name, lang_pats) or _matches_any(name, wild_pats):
        name_mult = 1.5
    elif _matches_any(name, UTILITY_PATTERNS):
        name_mult = 0.3

    # Framework multiplier
    framework_mult = 1.0
    for _fw, (path_parts, ext, mult) in FRAMEWORK_PATH_PATTERNS.items():
        if file_path.endswith(ext):
            for part in path_parts:
                if part in file_path:
                    framework_mult = max(framework_mult, mult)
                    break

    return base * export_mult * name_mult * framework_mult

def score_all_entry_points(
    nodes: Dict[str, dict],
    adjacency: Dict[str, List[str]],
    reverse_adjacency: Dict[str, List[str]],
) -> List[Tuple[str, float]]:
    """Score every node, return descending-sorted list of (entity_id, score)."""
    scored: List[Tuple[str, float]] = []
    for eid, node in nodes.items():
        if is_test_file(node.get("file_path", "")):
            continue
        callee_count = len(adjacency.get(eid, []))
        caller_count = len(reverse_adjacency.get(eid, []))
        scored.append((eid, score_entry_point(node, callee_count, caller_count)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

# ---------------------------------------------------------------------------
# Flow tracing (BFS)
# ---------------------------------------------------------------------------
def trace_flow(
    entry_id: str, adjacency: Dict[str, List[str]],
    max_depth: int = 10, max_branching: int = 4,
) -> List[List[str]]:
    """BFS from entry_id following CALLS edges. Returns list of traces."""
    traces: List[List[str]] = []
    queue: deque[Tuple[str, List[str]]] = deque([(entry_id, [entry_id])])
    visited_paths: Set[str] = set()
    max_iterations = 2000
    iterations = 0

    while queue and iterations < max_iterations:
        iterations += 1
        current, path = queue.popleft()
        callees = adjacency.get(current, [])

        if not callees or len(path) >= max_depth:
            if len(path) >= 2:
                key = "->".join(path)
                if key not in visited_paths:
                    visited_paths.add(key)
                    traces.append(path)
            continue

        expanded = False
        for callee in callees[:max_branching]:
            if callee in path:  # cycle avoidance
                continue
            expanded = True
            queue.append((callee, path + [callee]))

        if not expanded and len(path) >= 2:
            key = "->".join(path)
            if key not in visited_paths:
                visited_paths.add(key)
                traces.append(path)

    return traces

# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def deduplicate_traces(all_traces: List[List[str]]) -> List[List[str]]:
    """Remove subset traces; keep longest per (entry, terminal) pair."""
    if not all_traces:
        return []

    # Keep longest trace per (entry, terminal) pair
    best: Dict[Tuple[str, str], List[str]] = {}
    for trace in all_traces:
        key = (trace[0], trace[-1])
        if key not in best or len(trace) > len(best[key]):
            best[key] = trace
    candidates = sorted(best.values(), key=len, reverse=True)

    # Remove strict subsequences
    def is_subsequence(short: List[str], long: List[str]) -> bool:
        if len(short) >= len(long):
            return False
        it = iter(long)
        return all(item in it for item in short)

    kept: List[List[str]] = []
    for trace in candidates:
        if not any(is_subsequence(trace, longer) for longer in kept):
            kept.append(trace)
    return kept

# ---------------------------------------------------------------------------
# Community classification
# ---------------------------------------------------------------------------
def classify_trace(trace: List[str], memberships: Optional[Dict[str, str]]) -> str:
    """Classify trace as intra_community, cross_community, or unknown."""
    if not memberships:
        return "unknown"
    comms = {memberships[eid] for eid in trace if eid in memberships}
    if len(comms) == 0:
        return "unknown"
    return "intra_community" if len(comms) == 1 else "cross_community"

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
async def store_flows(
    conn: asyncpg.Connection, processes: List[dict], graph_name: str,
) -> Tuple[int, int]:
    """Create Process nodes and STEP_IN_PROCESS edges. Returns (nodes, edges)."""
    if not processes:
        return 0, 0

    node_success = 0
    for proc in processes:
        try:
            await conn.execute(f"""
                SELECT * FROM cypher('{graph_name}', $$
                    CREATE (:Process {{
                        process_id: '{escape_cypher(proc["process_id"])}',
                        name: '{escape_cypher(proc["name"])}',
                        process_type: '{escape_cypher(proc["process_type"])}',
                        step_count: {proc["step_count"]},
                        entry_point_id: '{escape_cypher(proc["entry_point_id"])}',
                        terminal_id: '{escape_cypher(proc["terminal_id"])}',
                        repo: '{escape_cypher(proc["repo"])}',
                        extraction_run_id: '{escape_cypher(proc["extraction_run_id"])}'
                    }})
                $$) as (result agtype);""")
            node_success += 1
        except Exception as e:
            logger.warning(f"Failed to create Process node {proc['process_id']}: {e}")

    # Load graph IDs for Process nodes and Function nodes
    proc_rows = await conn.fetch(f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (p:Process) RETURN p.process_id, id(p)
        $$) as (process_id agtype, gid agtype);""")
    proc_gid: Dict[str, int] = {
        str(r["process_id"]).strip('"'): int(str(r["gid"])) for r in proc_rows}

    func_rows = await conn.fetch(f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (n) WHERE n.entity_id IS NOT NULL RETURN n.entity_id, id(n)
        $$) as (entity_id agtype, gid agtype);""")
    func_gid: Dict[str, int] = {
        str(r["entity_id"]).strip('"'): int(str(r["gid"])) for r in func_rows}

    # Build edge values
    edge_values: List[str] = []
    for proc in processes:
        pgid = proc_gid.get(proc["process_id"])
        if pgid is None:
            continue
        for step_idx, step_eid in enumerate(proc["steps"], start=1):
            fgid = func_gid.get(step_eid)
            if fgid is None:
                continue
            props = f'{{"step": {step_idx}}}'
            edge_values.append(
                f"(graphid_in('{fgid}'), graphid_in('{pgid}'), '{props}'::agtype)")

    # Batch insert edges
    edge_success = 0
    BATCH = 500
    for i in range(0, len(edge_values), BATCH):
        batch = edge_values[i:i + BATCH]
        try:
            await conn.execute(
                f'INSERT INTO {graph_name}."STEP_IN_PROCESS" '
                f"(start_id, end_id, properties) VALUES {', '.join(batch)} "
                f"ON CONFLICT DO NOTHING")
            edge_success += len(batch)
        except Exception as e:
            logger.warning(f"STEP_IN_PROCESS batch insert failed at offset {i}: {e}")

    return node_success, edge_success

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
async def detect_flows(
    conn: asyncpg.Connection, repo: str, run_id: str,
    memberships: Optional[Dict[str, str]] = None,
    graph_name: str = DEFAULT_GRAPH,
    top_entries: int = 200, max_processes: int = 75,
    dry_run: bool = False,
) -> List[dict]:
    """Main orchestrator: score, trace, deduplicate, classify, store."""
    t0 = time.monotonic()

    nodes, adj, rev_adj = await pull_call_graph(conn, repo, graph_name)
    if not nodes:
        logger.warning(f"No callable nodes found for repo '{repo}'")
        return []

    scored = score_all_entry_points(nodes, adj, rev_adj)
    if scored:
        logger.info(f"Scored {len(scored)} candidates; top={scored[0][1]:.3f} "
                     f"({nodes[scored[0][0]]['name']})")

    # BFS traces
    all_traces: List[List[str]] = []
    for eid, _ in scored[:top_entries]:
        all_traces.extend(trace_flow(eid, adj, max_depth=10, max_branching=4))
    logger.info(f"Raw traces: {len(all_traces)}")

    unique = deduplicate_traces(all_traces)
    logger.info(f"After dedup: {len(unique)}")

    unique.sort(key=len, reverse=True)
    unique = unique[:max_processes]

    # Build process records
    processes: List[dict] = []
    for idx, trace in enumerate(unique):
        entry_name = nodes[trace[0]]["name"] if trace[0] in nodes else trace[0]
        terminal_name = nodes[trace[-1]]["name"] if trace[-1] in nodes else trace[-1]
        proc_id = re.sub(r"[^a-z0-9_]", "_", f"proc_{idx}_{entry_name.lower()[:40]}")
        processes.append({
            "process_id": proc_id,
            "name": f"{entry_name} -> {terminal_name}",
            "process_type": classify_trace(trace, memberships),
            "step_count": len(trace),
            "entry_point_id": trace[0],
            "terminal_id": trace[-1],
            "repo": repo,
            "extraction_run_id": run_id,
            "steps": trace,
        })

    t_analysis = time.monotonic() - t0
    logger.info(f"Analysis: {len(processes)} processes in {t_analysis:.2f}s")

    type_counts = defaultdict(int)
    for p in processes:
        type_counts[p["process_type"]] += 1
    for ptype, cnt in sorted(type_counts.items()):
        logger.info(f"  {ptype}: {cnt}")
    for p in processes[:10]:
        logger.info(f"  [{p['step_count']} steps] [{p['process_type']}] {p['name']}")
    if len(processes) > 10:
        logger.info(f"  ... and {len(processes) - 10} more")

    if dry_run:
        logger.info("DRY RUN -- skipping storage")
        return processes

    await ensure_labels(conn, graph_name)
    await cleanup_old_flows(conn, repo, graph_name)

    t_store = time.monotonic()
    nc, ec = await store_flows(conn, processes, graph_name)
    logger.info(f"Stored {nc} Process nodes, {ec} STEP_IN_PROCESS edges "
                f"in {time.monotonic() - t_store:.2f}s")
    return processes

# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------
async def _get_all_repos(conn: asyncpg.Connection, graph_name: str) -> List[str]:
    rows = await conn.fetch(f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (n) WHERE n.repo IS NOT NULL RETURN DISTINCT n.repo
        $$) as (repo agtype);""")
    return sorted({str(r["repo"]).strip('"') for r in rows})

async def main() -> None:
    parser = argparse.ArgumentParser(description="Detect execution flows in the code graph")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--repo", help="Repository name to process")
    grp.add_argument("--all-repos", action="store_true", help="Process all repos")
    parser.add_argument("--dry-run", action="store_true", help="Score/trace without writing")
    parser.add_argument("--graph-name", default=DEFAULT_GRAPH, help="AGE graph name")
    parser.add_argument("--top-entries", type=int, default=200, help="Max entry candidates")
    parser.add_argument("--max-processes", type=int, default=75, help="Max processes per repo")
    parser.add_argument("--run-id", default=None, help="Extraction run ID (auto if omitted)")
    args = parser.parse_args()

    run_id = args.run_id or hashlib.sha256(
        f"flows:{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16]

    db_url = (f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
              f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")

    logger.info("=" * 60)
    logger.info("EXECUTION FLOW DETECTION")
    logger.info("=" * 60)
    logger.info(f"Graph: {args.graph_name}  Run: {run_id}  "
                f"Top: {args.top_entries}  Max: {args.max_processes}  Dry: {args.dry_run}")

    try:
        conn = await asyncpg.connect(db_url)
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        sys.exit(1)

    try:
        await setup_age(conn)
        repos = await _get_all_repos(conn, args.graph_name) if args.all_repos else [args.repo]
        if args.all_repos:
            logger.info(f"Found {len(repos)} repos: {repos}")

        total = 0
        t_start = time.monotonic()
        for repo in repos:
            logger.info(f"\n{'~'*50}\nProcessing: {repo}\n{'~'*50}")
            procs = await detect_flows(
                conn, repo, run_id, memberships=None, graph_name=args.graph_name,
                top_entries=args.top_entries, max_processes=args.max_processes,
                dry_run=args.dry_run)
            total += len(procs)

        elapsed = time.monotonic() - t_start
        logger.info(f"\n{'='*60}\nCOMPLETE  Repos: {len(repos)}  "
                     f"Processes: {total}  Time: {elapsed:.2f}s\n{'='*60}")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
