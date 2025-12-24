#!/usr/bin/env python3
"""
Week 7 Canary Validation Script - FIX-013/014 Verification

Validates that FIX-013 (block PROCESS for code modules) and FIX-014 (block
MATERIAL for abstract concepts) are working correctly on new extraction output.

IMPORTANT: This script runs in DRY-RUN mode by default - no writes to production DB.

Usage:
    cd /opt/projects/koi-processor
    set -a; source .env; set +a
    PYTHONPATH=src python scripts/validation/week7_canary_fix013_fix014.py

    # Run with persistence (USE WITH CAUTION - not recommended for canary)
    PYTHONPATH=src python scripts/validation/week7_canary_fix013_fix014.py --persist

Author: Claude Code
Date: 2025-12-24
Version: 1.0.0
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, field, asdict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor

from extraction.gemini_extractor import GeminiExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator


@dataclass
class CanaryResult:
    """Result of canary validation."""
    run_id: str
    timestamp: str
    mode: str  # "dry_run" or "persist"
    docs_processed: int
    docs_succeeded: int
    docs_failed: int

    # Entity counts
    total_entities_extracted: int
    total_entities_passed: int
    total_entities_blocked: int

    # FIX-013 validation
    fix013_code_module_as_process_blocked: int
    fix013_code_module_examples: List[Dict]
    fix013_false_negatives: List[Dict]  # Code modules that slipped through

    # FIX-014 validation
    fix014_abstract_concept_as_material_blocked: int
    fix014_concept_examples: List[Dict]
    fix014_false_negatives: List[Dict]  # Abstract concepts that slipped through

    # Other quality gate checks
    http_uris_found: int
    entity_type_violations: int
    humanactor_violations: int
    self_referential_blocked: int

    # Pass/Fail status
    fix013_passed: bool = False
    fix014_passed: bool = False
    quality_gates_passed: bool = False
    overall_passed: bool = False

    # Targeting info
    must_contain_patterns: List[str] = field(default_factory=list)
    selection_mode: str = "random"

    # Notes
    notes: List[str] = field(default_factory=list)


# Corpus filter for natural-language documents (same as stage6_canary_gemini.py)
CORPUS_FILTER_SQL = r"""
  AND (
    (source_sensor NOT ILIKE '%%github%%' AND source_sensor NOT ILIKE '%%gitlab%%')
    OR
    (
      (source_sensor ILIKE '%%github%%' OR source_sensor ILIKE '%%gitlab%%')
      AND (metadata ? 'file_path')
      AND (metadata->>'file_path') IS NOT NULL
      AND (
        (metadata->>'file_path') ~* '[.](md|mdx|rst|txt)$'
        OR (metadata->>'file_path') ~* '(^|/)(readme|license|changelog)([.].*)?$'
        OR (metadata->>'file_path') ILIKE '%%/docs/%%'
      )
      AND (metadata->>'file_path') NOT ILIKE '%%.pb.go'
      AND (metadata->>'file_path') !~* '/(node_modules|vendor|dist|build|generated)/'
      AND (metadata->>'file_path') !~* '/(test|tests|examples)/'
      AND (metadata->>'file_path') !~* '_test[.][^/]+$'
    )
  )
"""

# FIX-013: Code module patterns that should be blocked as PROCESS
CODE_MODULE_PATTERNS = [
    'entityqualityfilter', 'entity quality filter',
    'canonicalresolver', 'canonical resolver',
    'confidencefilter', 'confidence filter',
    'documentleveldeduplicator', 'document level deduplicator',
    'ontologynormalizer', 'ontology normalizer',
    'listsplitter', 'list splitter',
    'dataloader', 'configparser', 'requesthandler',
    'responsehandler', 'eventlistener', 'messagequeue',
    'taskqueue', 'jobrunner', 'taskrunner',
]

# FIX-014: Abstract concepts that should be blocked as MATERIAL
ABSTRACT_CONCEPTS = [
    'biodiversity', 'ecosystem', 'ecology',
    'carbon sequestration', 'carbon capture', 'carbon offset',
    'sustainability', 'regeneration', 'restoration',
    'conservation', 'preservation', 'resilience',
    'ecosystem services', 'ecological services',
    'ecological assets', 'natural assets', 'natural capital',
    'carbon credits', 'carbon credit', 'offset credits',
    'biodiversity credits', 'nature credits',
    'verification', 'validation', 'monitoring',
    'governance', 'stewardship', 'commons', 'public goods',
]


def infer_source_type(source_sensor: str) -> str:
    """Infer source_type from source_sensor string."""
    s = (source_sensor or "").lower()
    if "discourse" in s:
        return "discourse"
    if "github" in s:
        return "github"
    if "gitlab" in s:
        return "github"
    if "medium" in s:
        return "medium"
    if "twitter" in s:
        return "twitter"
    return "website"


def is_camelcase(s: str) -> bool:
    """Check if string is CamelCase (potential code module)."""
    import re
    return bool(re.match(r'^[A-Z][a-z]+(?:[A-Z][a-z]+)+$', s))


def check_fix013_violations(entities: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    Check for FIX-013 violations in passed entities.

    Returns:
        Tuple of (code_modules_correctly_blocked, false_negatives)
    """
    false_negatives = []

    for e in entities:
        name = e.get('name', '').strip()
        etype = e.get('type', '').upper()

        if etype != 'PROCESS':
            continue

        name_lower = name.lower()

        # Check if it's a code module that slipped through
        if name_lower in CODE_MODULE_PATTERNS or is_camelcase(name):
            false_negatives.append({
                'name': name,
                'type': etype,
                'reason': 'code_module_as_process_should_be_blocked'
            })

    return false_negatives


def check_fix014_violations(entities: List[Dict]) -> List[Dict]:
    """
    Check for FIX-014 violations in passed entities.

    Returns:
        List of false negatives (abstract concepts that slipped through as MATERIAL)
    """
    false_negatives = []

    for e in entities:
        name = e.get('name', '').strip()
        etype = e.get('type', '').upper()

        if etype != 'MATERIAL':
            continue

        name_lower = name.lower()

        # Check if it's an abstract concept that slipped through
        if name_lower in ABSTRACT_CONCEPTS:
            false_negatives.append({
                'name': name,
                'type': etype,
                'reason': 'abstract_concept_as_material_should_be_blocked'
            })

    return false_negatives


async def main(persist: bool = False, limit: int = 10, must_contain: List[str] = None):
    """Run canary validation on N random documents.

    Args:
        persist: If True, persist entities to database
        limit: Number of documents to process
        must_contain: Optional list of patterns - only select docs containing these
    """
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mode = "persist" if persist else "dry_run"

    print(f"{'=' * 70}")
    print(f"WEEK 7/8 CANARY VALIDATION - FIX-013/014")
    print(f"{'=' * 70}")
    print(f"Run ID: {run_id}")
    print(f"Mode: {mode.upper()}")
    print(f"Documents: {limit}")
    if must_contain:
        print(f"Must Contain: {', '.join(must_contain)}")
    else:
        print(f"Selection: RANDOM")
    print()

    if persist:
        print("WARNING: Running in PERSIST mode - entities WILL be written to database!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        import time
        time.sleep(5)

    result = CanaryResult(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        docs_processed=0,
        docs_succeeded=0,
        docs_failed=0,
        total_entities_extracted=0,
        total_entities_passed=0,
        total_entities_blocked=0,
        fix013_code_module_as_process_blocked=0,
        fix013_code_module_examples=[],
        fix013_false_negatives=[],
        fix014_abstract_concept_as_material_blocked=0,
        fix014_concept_examples=[],
        fix014_false_negatives=[],
        http_uris_found=0,
        entity_type_violations=0,
        humanactor_violations=0,
        self_referential_blocked=0,
        must_contain_patterns=must_contain or [],
        selection_mode="targeted" if must_contain else "random",
    )

    # Initialize extractor
    extractor = GeminiExtractor()
    print(f"[canary] GeminiExtractor initialized (model={os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')})")

    # Initialize KnowledgeGraphIntegrator with pipeline
    kg = KnowledgeGraphIntegrator(
        store_type="memory",
        use_pipeline=True,
        enable_deduplication=True
    )
    pipeline_modules = getattr(kg.pipeline, "modules", None)
    pipeline_len = len(pipeline_modules) if pipeline_modules is not None else 0
    print(f"[canary] KnowledgeGraphIntegrator initialized (pipeline modules: {pipeline_len})")

    if not kg.pipeline:
        print("[ERROR] Pipeline not initialized")
        return result

    # Connect to PostgreSQL
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    print(f"[canary] Connected to PostgreSQL (dry_run={not persist})")

    # Build must_contain filter if patterns are provided
    must_contain_filter = ""
    query_params = [limit]
    if must_contain:
        # Build ILIKE conditions for each pattern
        patterns = []
        for i, pattern in enumerate(must_contain, start=2):
            patterns.append(f"content->>'text' ILIKE ${i}")
            query_params.append(f"%{pattern}%")
        must_contain_filter = " AND (" + " OR ".join(patterns) + ")"
        # Prepend limit param
        query_params = [limit] + [f"%{p}%" for p in must_contain]

    # Fetch documents (random or targeted)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if must_contain:
            # Targeted selection - prioritize docs containing the patterns
            pattern_filters = " OR ".join([f"content->>'text' ILIKE %s" for _ in must_contain])
            cur.execute(
                f"""
                SELECT
                  id,
                  rid,
                  source_sensor,
                  metadata->>'file_path' AS file_path,
                  content->>'text' AS text
                FROM koi_memories
                WHERE superseded_at IS NULL
                  AND content->>'text' IS NOT NULL
                  AND LENGTH(content->>'text') > 200
                  {CORPUS_FILTER_SQL}
                  AND ({pattern_filters})
                ORDER BY RANDOM()
                LIMIT %s
                """,
                [f"%{p}%" for p in must_contain] + [limit],
            )
        else:
            # Random selection
            cur.execute(
                f"""
                SELECT
                  id,
                  rid,
                  source_sensor,
                  metadata->>'file_path' AS file_path,
                  content->>'text' AS text
                FROM koi_memories
                WHERE superseded_at IS NULL
                  AND content->>'text' IS NOT NULL
                  AND LENGTH(content->>'text') > 200
                  {CORPUS_FILTER_SQL}
                ORDER BY RANDOM()
                LIMIT %s
                """,
                (limit,),
            )
        docs = cur.fetchall()

    print(f"[canary] Fetched {len(docs)} documents")
    print()

    # Track all blocked entities for analysis
    all_blocked_entities = []

    for i, doc in enumerate(docs, 1):
        fp = (doc.get("file_path") or "").strip()
        print(f"[{i}/{len(docs)}] Processing rid={doc['rid'][:50]}...")

        result.docs_processed += 1

        try:
            source_type = infer_source_type(doc["source_sensor"])

            # Step 1: Extract with Gemini
            extraction = await extractor.extract_metadata(
                doc["text"],
                source_type,
                existing_metadata={"rid": doc["rid"]},
            )

            raw_entities = extraction.get("extracted_entities", [])
            raw_relationships = extraction.get("extracted_relationships", [])
            tokens = extraction.get("token_usage", {}).get("total_tokens", 0)

            print(f"  Gemini: entities={len(raw_entities)} rels={len(raw_relationships)} tokens={tokens}")

            result.total_entities_extracted += len(raw_entities)

            # Step 2: Run pipeline (this is where FIX-013/014 filters apply)
            context = kg.pipeline.process_entities(
                raw_entities,
                raw_relationships,
                metadata={"memory_rid": doc["rid"], "run_id": run_id, "source_type": source_type},
            )

            passed_entities = context.entities
            blocked_entities = context.blocked_entities

            result.total_entities_passed += len(passed_entities)
            result.total_entities_blocked += len(blocked_entities)

            print(f"  Pipeline: passed={len(passed_entities)} blocked={len(blocked_entities)}")

            # Analyze blocked entities for FIX-013/014 effectiveness
            for blocked in blocked_entities:
                # Entity is a dataclass, access attributes directly
                reason = blocked.metadata.get('block_reason', blocked.metadata.get('reason', ''))
                name = blocked.name
                etype = blocked.type

                if reason == 'code_module_as_process':
                    result.fix013_code_module_as_process_blocked += 1
                    if len(result.fix013_code_module_examples) < 10:
                        result.fix013_code_module_examples.append({
                            'name': name,
                            'type': etype,
                            'reason': reason
                        })

                elif reason == 'abstract_concept_as_material':
                    result.fix014_abstract_concept_as_material_blocked += 1
                    if len(result.fix014_concept_examples) < 10:
                        result.fix014_concept_examples.append({
                            'name': name,
                            'type': etype,
                            'reason': reason
                        })

                all_blocked_entities.append(blocked)

            # Check for false negatives in passed entities
            fix013_violations = check_fix013_violations(
                [{'name': e.name, 'type': e.type} for e in passed_entities]
            )
            fix014_violations = check_fix014_violations(
                [{'name': e.name, 'type': e.type} for e in passed_entities]
            )

            result.fix013_false_negatives.extend(fix013_violations)
            result.fix014_false_negatives.extend(fix014_violations)

            if fix013_violations:
                print(f"  [WARN] FIX-013 false negatives: {len(fix013_violations)}")
            if fix014_violations:
                print(f"  [WARN] FIX-014 false negatives: {len(fix014_violations)}")

            # Step 3: Only persist if not dry-run
            if persist and kg.entity_resolver:
                seen_entities = set()
                for e in passed_entities:
                    key = (e.name, e.type)
                    if key in seen_entities:
                        continue
                    seen_entities.add(key)
                    kg.entity_resolver.get_or_create_entity(
                        e.name, e.type,
                        metadata={"doc_rid": doc["rid"], "run_id": run_id}
                    )
                print(f"  Persisted: {len(seen_entities)} entities")

            result.docs_succeeded += 1

        except Exception as e:
            print(f"  [ERROR] Failed: {e}")
            import traceback
            traceback.print_exc()
            result.docs_failed += 1
            result.notes.append(f"Doc {doc['rid'][:30]} failed: {str(e)[:100]}")

    conn.close()

    # Run quality gate queries (only in persist mode, against actual DB)
    print()
    print(f"{'=' * 70}")
    print("VALIDATION RESULTS")
    print(f"{'=' * 70}")

    # Determine pass/fail
    result.fix013_passed = len(result.fix013_false_negatives) == 0
    result.fix014_passed = len(result.fix014_false_negatives) == 0
    result.quality_gates_passed = (
        result.http_uris_found == 0 and
        result.entity_type_violations == 0 and
        result.humanactor_violations == 0
    )
    result.overall_passed = (
        result.fix013_passed and
        result.fix014_passed and
        result.docs_failed == 0
    )

    print(f"""
Documents Processed: {result.docs_processed}
  - Succeeded: {result.docs_succeeded}
  - Failed: {result.docs_failed}

Entity Counts:
  - Total Extracted (Gemini): {result.total_entities_extracted}
  - Passed Pipeline: {result.total_entities_passed}
  - Blocked by Pipeline: {result.total_entities_blocked}

FIX-013 (Block PROCESS for Code Modules):
  - Blocked: {result.fix013_code_module_as_process_blocked}
  - False Negatives: {len(result.fix013_false_negatives)}
  - Status: {'PASS' if result.fix013_passed else 'FAIL'}
""")

    if result.fix013_code_module_examples:
        print("  Examples blocked:")
        for ex in result.fix013_code_module_examples[:5]:
            print(f"    - {ex['name']} ({ex['type']})")

    if result.fix013_false_negatives:
        print("  FALSE NEGATIVES (entities that slipped through):")
        for fn in result.fix013_false_negatives[:5]:
            print(f"    - {fn['name']} ({fn['type']})")

    print(f"""
FIX-014 (Block MATERIAL for Abstract Concepts):
  - Blocked: {result.fix014_abstract_concept_as_material_blocked}
  - False Negatives: {len(result.fix014_false_negatives)}
  - Status: {'PASS' if result.fix014_passed else 'FAIL'}
""")

    if result.fix014_concept_examples:
        print("  Examples blocked:")
        for ex in result.fix014_concept_examples[:5]:
            print(f"    - {ex['name']} ({ex['type']})")

    if result.fix014_false_negatives:
        print("  FALSE NEGATIVES (entities that slipped through):")
        for fn in result.fix014_false_negatives[:5]:
            print(f"    - {fn['name']} ({fn['type']})")

    print(f"""
{'=' * 70}
OVERALL STATUS: {'CANARY PASSED' if result.overall_passed else 'CANARY FAILED'}
{'=' * 70}
""")

    if not result.overall_passed:
        print("Issues found:")
        if not result.fix013_passed:
            print(f"  - FIX-013: {len(result.fix013_false_negatives)} code modules slipped through as PROCESS")
        if not result.fix014_passed:
            print(f"  - FIX-014: {len(result.fix014_false_negatives)} abstract concepts slipped through as MATERIAL")
        if result.docs_failed > 0:
            print(f"  - {result.docs_failed} documents failed to process")

    # Generate report
    report_path = Path(__file__).parent.parent.parent / "docs" / "archive" / "reports" / "week7_canary_validation_fix013_fix014.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    generate_report(result, report_path)
    print(f"\nReport written to: {report_path}")

    return result


def generate_report(result: CanaryResult, output_path: Path):
    """Generate markdown report."""
    lines = [
        "# Week 7/8 Canary Validation Report - FIX-013/014",
        "",
        f"**Generated:** {result.timestamp}",
        f"**Run ID:** {result.run_id}",
        f"**Mode:** {result.mode.upper()} (no production writes)" if result.mode == "dry_run" else f"**Mode:** {result.mode.upper()}",
        f"**Selection:** {result.selection_mode.upper()}",
    ]

    if result.must_contain_patterns:
        lines.append(f"**Must Contain:** {', '.join(result.must_contain_patterns)}")

    lines.extend([
        f"**Status:** {'PASS' if result.overall_passed else 'FAIL'}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Documents Processed | {result.docs_processed} |",
        f"| Documents Succeeded | {result.docs_succeeded} |",
        f"| Documents Failed | {result.docs_failed} |",
        f"| Entities Extracted | {result.total_entities_extracted} |",
        f"| Entities Passed Pipeline | {result.total_entities_passed} |",
        f"| Entities Blocked | {result.total_entities_blocked} |",
        "",
        "---",
        "",
        "## FIX-013: Block PROCESS for Code Module Names",
        "",
        f"**Status:** {'PASS' if result.fix013_passed else 'FAIL'}",
        "",
        f"- Entities blocked by FIX-013: **{result.fix013_code_module_as_process_blocked}**",
        f"- False negatives (slipped through): **{len(result.fix013_false_negatives)}**",
        "",
    ])

    if result.fix013_code_module_examples:
        lines.extend([
            "### Examples Correctly Blocked",
            "",
            "| Entity Name | Type | Block Reason |",
            "|-------------|------|--------------|",
        ])
        for ex in result.fix013_code_module_examples[:10]:
            lines.append(f"| {ex['name']} | {ex['type']} | {ex['reason']} |")
        lines.append("")

    if result.fix013_false_negatives:
        lines.extend([
            "### FALSE NEGATIVES (Should Have Been Blocked)",
            "",
            "| Entity Name | Type | Issue |",
            "|-------------|------|-------|",
        ])
        for fn in result.fix013_false_negatives:
            lines.append(f"| {fn['name']} | {fn['type']} | {fn['reason']} |")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## FIX-014: Block MATERIAL for Abstract Concepts",
        "",
        f"**Status:** {'PASS' if result.fix014_passed else 'FAIL'}",
        "",
        f"- Entities blocked by FIX-014: **{result.fix014_abstract_concept_as_material_blocked}**",
        f"- False negatives (slipped through): **{len(result.fix014_false_negatives)}**",
        "",
    ])

    if result.fix014_concept_examples:
        lines.extend([
            "### Examples Correctly Blocked",
            "",
            "| Entity Name | Type | Block Reason |",
            "|-------------|------|--------------|",
        ])
        for ex in result.fix014_concept_examples[:10]:
            lines.append(f"| {ex['name']} | {ex['type']} | {ex['reason']} |")
        lines.append("")

    if result.fix014_false_negatives:
        lines.extend([
            "### FALSE NEGATIVES (Should Have Been Blocked)",
            "",
            "| Entity Name | Type | Issue |",
            "|-------------|------|-------|",
        ])
        for fn in result.fix014_false_negatives:
            lines.append(f"| {fn['name']} | {fn['type']} | {fn['reason']} |")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## Quality Gates",
        "",
        "| Gate | Check | Result |",
        "|------|-------|--------|",
        f"| FIX-013 | No code modules as PROCESS | {'PASS' if result.fix013_passed else 'FAIL'} |",
        f"| FIX-014 | No abstract concepts as MATERIAL | {'PASS' if result.fix014_passed else 'FAIL'} |",
        f"| Extraction | All documents processed | {'PASS' if result.docs_failed == 0 else 'FAIL'} |",
        "",
        "---",
        "",
        "## Command Used",
        "",
        "```bash",
        "cd /opt/projects/koi-processor",
        "set -a; source .env; set +a",
        "PYTHONPATH=src python scripts/validation/week7_canary_fix013_fix014.py",
        "```",
        "",
        "---",
        "",
        "## Environment",
        "",
        f"- **Database Target:** PostgreSQL (localhost:5433/eliza)",
        f"- **Mode:** {result.mode.upper()}",
        f"- **Production Data Modified:** {'YES' if result.mode == 'persist' else 'NO'}",
        "",
        "---",
        "",
        "*Report generated by `scripts/validation/week7_canary_fix013_fix014.py`*",
    ])

    if result.notes:
        lines.extend([
            "",
            "## Notes",
            "",
        ])
        for note in result.notes:
            lines.append(f"- {note}")

    output_path.write_text("\n".join(lines))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Week 7/8 Canary Validation - FIX-013/014")
    parser.add_argument("--persist", action="store_true",
                       help="Actually persist entities to database (default: dry-run)")
    parser.add_argument("--limit", type=int, default=10,
                       help="Number of documents to process (default: 10)")
    parser.add_argument("--must-contain", type=str, action="append", dest="must_contain",
                       help="Select docs containing this pattern (can be repeated)")
    args = parser.parse_args()

    asyncio.run(main(
        persist=args.persist,
        limit=args.limit,
        must_contain=args.must_contain
    ))
