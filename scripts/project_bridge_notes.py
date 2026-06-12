#!/usr/bin/env python3
"""
Learning Field Graph Projection — Phase 1, Step 7

Projects bridge notes from Spore, Intelligence Commons (IC), Flow Coding (FC),
Poietic Match (PM), and bioregional-coordination into the KOI knowledge graph
as structured Claim, Concept, and Question entities with argumentative edges
(supports/opposes).

Within Spore's graph-projections architecture (spore:ADR-0058 / spore:ADR-0070),
this script operates as one infrastructure surface inside the Epistemic primary's
KOI materialization — bridge-note intake — and does not itself encode the full
3-primary + 5-view-template taxonomy. See spore:ADR-0071 for the cross-repo
scope clarification and pm:ADR-0016 for the PM-side canon realignment.

Two claim layers:
  - Source claims: extracted from bridge note Claim Registers
  - Review claims: about proposed canon changes (derived from relates_to × concept)

Usage:
  python scripts/project_bridge_notes.py --dry-run          # preview what would be created
  python scripts/project_bridge_notes.py --apply            # create entities and edges
  python scripts/project_bridge_notes.py --apply --note <path>  # single note
  python scripts/project_bridge_notes.py --parse-report --note <path>  # parse only, no DB
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

try:
    import asyncpg
except ImportError:  # pragma: no cover - exercised by pure parser environments
    asyncpg = None

try:
    import httpx
except ImportError:  # pragma: no cover - exercised by pure parser environments
    httpx = None

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("project_bridge_notes")

KOI_BASE = "http://localhost:8351"


def _claims_service_token() -> Optional[str]:
    """Token for the auth-gated /claims/ write path."""
    tok = os.getenv("KOI_CLAIMS_SERVICE_TOKEN")
    if tok:
        return tok.strip()
    p = Path.home() / ".config/personal-koi/koi-state/claims_service_token"
    if p.exists():
        return p.read_text().strip()
    return None


def _http_error_snippet(text: str, limit: int = 500) -> str:
    return text[:limit].replace("\n", " ")


async def verify_claims_auth(client: httpx.AsyncClient) -> None:
    """Fail before direct SQL writes if the claims API token is missing/rejected."""
    resp = await client.get(f"{KOI_BASE}/claims/identity", timeout=10)
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"/claims auth failed ({resp.status_code}); token missing or rejected. "
            "Source config/personal.env or provide KOI_CLAIMS_SERVICE_TOKEN."
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"/claims auth preflight failed: {resp.status_code} "
            f"{_http_error_snippet(resp.text)}"
        )

PROJECTS = {
    "spore": {
        "project_id": "spore",
        "claimant_uri": "org:spore-learning-field",
        "bridge_dir": Path.home() / "projects/spore/docs/research/connections",
    },
    "ic": {
        "project_id": "ic",
        "claimant_uri": "org:ic-learning-field",
        "bridge_dir": Path.home() / "projects/intelligence-commons/docs/research",
    },
    "fc": {
        "project_id": "fc",
        "claimant_uri": "org:flow-coding-learning-field",
        "bridge_dir": Path.home() / "projects/flowcoding/docs/research/connections",
    },
    "pm": {
        "project_id": "pm",
        "claimant_uri": "org:poietic-match-learning-field",
        "bridge_dir": Path.home() / "projects/poietic-match/docs/research/connections",
    },
    "bioregional-coordination": {
        "project_id": "bioregional-coordination",
        "claimant_uri": "org:bioregional-coordination-learning-field",
        "bridge_dir": Path.home() / "projects/bioregional-coordination/docs/research/connections",
    },
    "bioregional-mapping": {
        "project_id": "bioregional-mapping",
        "claimant_uri": "org:bioregional-mapping-learning-field",
        "bridge_dir": Path.home() / "projects/bioregional-mapping/docs/research/connections",
    },
    "bioregional-economics": {
        "project_id": "bioregional-economics",
        "claimant_uri": "org:bioregional-economics-learning-field",
        "bridge_dir": Path.home() / "projects/bioregional-economics/docs/research/connections",
    },
}

DISPOSITION_SLUG = {
    "clarify existing term": "clarify",
    "candidate primitive": "propose-primitive",
    "candidate pattern": "propose-pattern",
    "candidate protocol": "propose-protocol",
    "implementation hypothesis": "hypothesize",
    "novel synthesis": "synthesize",
    "unresolved tension": "resolve-tension",
    "no change": "no-change",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BridgeClaim:
    c_id: str           # e.g. "C1"
    confidence: str     # high | medium | low
    anchor: str         # e.g. "§What Spore Does Not Already Have — linguistic closure risk"
    statement: str      # claim text

@dataclass
class OpenQuestion:
    number: int
    question: str       # question text (bold part)
    context: str        # full text including context

@dataclass
class ReviewDirective:
    """Explicit review claim parsed from ## Review Claims section."""
    r_id: str                  # e.g. "R1"
    target_doc: str            # primary target doc (first if "or")
    concept: str               # explicit concept slug
    statement: str             # claim text
    supported_by: list         # list of C-IDs (e.g. ["C1", "C2"])

@dataclass
class BridgeNote:
    path: Path
    doc_id: str
    doc_kind: str
    status: str
    disposition: str
    research_subkind: str
    concepts: list
    depends_on: list
    relates_to: list
    claims: list        # list of BridgeClaim
    questions: list     # list of OpenQuestion
    review_directives: list  # list of ReviewDirective (may be empty)
    project_key: str    # "spore" or "ic"

@dataclass
class ParseIssue:
    severity: str       # error | warning
    code: str
    message: str
    line: int | None = None

@dataclass
class ParseReport:
    path: Path
    doc_id: str
    project_key: str
    c_ids: list[str]
    r_ids: list[str]
    raw_r_ids: list[str]
    issues: list[ParseIssue]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown."""
    m = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not m:
        raise ValueError("No YAML frontmatter found")
    return yaml.safe_load(m.group(1))


def parse_claims(text: str) -> list[BridgeClaim]:
    """Parse Claim Register section for C-ID entries."""
    # Find the Claim Register section (may or may not have number prefix)
    m = re.search(r'## (?:\d+\.\s+)?Claim Register\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    if not m:
        return []

    section = m.group(1)
    claims = []

    # Pattern: **C1** [confidence: high] [anchor: §section] Statement text
    pattern = re.compile(
        r'\*\*(C\d+)\*\*\s+'
        r'\[confidence:\s*(high|medium|low)\]\s+'
        r'\[anchor:\s*(.+?)\]\s*\n'
        r'(.*?)(?=\n\*\*C\d+\*\*|\n(?:-\s*)?\*\*R\d+\*\*|\Z)',
        re.DOTALL
    )

    for match in pattern.finditer(section):
        statement = match.group(4).strip()
        # Clean up any trailing whitespace/newlines
        statement = re.sub(r'\s+', ' ', statement)
        claims.append(BridgeClaim(
            c_id=match.group(1),
            confidence=match.group(2),
            anchor=match.group(3).strip(),
            statement=statement,
        ))

    return claims


def parse_questions(text: str) -> list[OpenQuestion]:
    """Parse Open Questions section (may have numbered header like '## 7. Open Questions')."""
    m = re.search(r'## (?:\d+\.\s+)?Open Questions\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    if not m:
        return []

    section = m.group(1)
    questions = []

    # Two formats:
    #   "1. **Question text?** Context..."
    #   "1. **Topic:** Context with question?..."
    # Capture the full numbered entry, then extract bold part + rest
    pattern = re.compile(
        r'(\d+)\.\s+\*\*(.+?)\*\*\s*(.*?)(?=\n\d+\.\s+\*\*|\Z)',
        re.DOTALL
    )

    for match in pattern.finditer(section):
        bold_part = match.group(2).strip()
        rest = match.group(3).strip()
        rest = re.sub(r'\s+', ' ', rest)
        # The "question" is the bold part; full text is bold + rest
        full_text = f"{bold_part} {rest}".strip() if rest else bold_part
        questions.append(OpenQuestion(
            number=int(match.group(1)),
            question=bold_part,
            context=full_text,
        ))

    return questions


def _extract_review_sections(text: str) -> list[tuple[str, int]]:
    """Return sections that may contain R-claim directives with start lines."""
    sections = []
    for header in [r'Review Claims', r'Claim Register']:
        pattern = re.compile(
            rf'## (?:\d+\.\s+)?{header}\s*\n(.*?)(?=\n## |\Z)',
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            section = match.group(1)
            if re.search(r'(?:^|\n)(?:-\s*)?\*\*R\d+\*\*', section):
                start_line = text.count("\n", 0, match.start(1)) + 1
                sections.append((section, start_line))
    return sections


def _primary_target(target_str: str, r_id: str) -> str:
    """Return the first target from a possibly 'a or b' target expression."""
    targets = [t.strip() for t in re.split(r'\s+or\s+', target_str.strip())]
    primary_target = targets[0]
    if len(targets) > 1:
        log.info(f"  {r_id}: target '{target_str}' → primary: {primary_target}")
    return primary_target


def _parse_supported_by(text: str) -> list[str]:
    """Extract C IDs from legacy or v2 supported_by syntax."""
    return re.findall(r'\bC\d+\b', text)


def _clean_review_statement(text: str) -> str:
    """Remove directive metadata from review-claim statement text."""
    text = re.sub(r'^\s*-\s*', '', text.strip())
    text = re.sub(r'^\*\*R\d+\*\*:\s*', '', text)
    text = re.sub(r'\*\*R\d+\*\*\s+\[review claim\]\s*', '', text)
    text = re.sub(r'\[target:\s*[^\]]+\]', '', text)
    text = re.sub(r'\[concept:\s*[^\]]+\]', '', text)
    text = text.replace("TODO: slug-deferred", "")
    text = re.sub(r'\n\s+supported_by:.*', '', text)
    text = re.sub(r'\n?\*R\d+\s+is supported by[^*]*\*', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_review_directives(text: str) -> list[ReviewDirective]:
    """Parse R-claim directives with explicit target + concept.

    Searches both '## Review Claims' and '## Claim Register' sections,
    since R-claims may appear alongside C-claims in the Claim Register.
    """
    directives = []
    sections = _extract_review_sections(text)
    if not sections:
        return directives

    # Pattern: **R1** [review claim] [target: doc.id] [concept: slug]
    # Statement text
    # *R1 is supported by C1, C2, C3.*
    legacy_pattern = re.compile(
        r'\*\*(R\d+)\*\*\s+'
        r'\[review claim\]\s+'
        r'\[target:\s*([^\]]+)\]\s+'
        r'\[concept:\s*([^\]]+)\]\s*\n'
        r'(.*?)(?=\n(?:-\s*)?\*\*R\d+\*\*|\Z)',
        re.DOTALL
    )

    # Pattern: - **R1**: Claim text. [target: doc.id] [concept: slug]
    #   supported_by: C1, C2.
    v2_pattern = re.compile(
        r'^\s*-\s+\*\*(R\d+)\*\*:\s*'
        r'(.*?)(?=^\s*-\s+\*\*R\d+\*\*:|^\s*\*\*R\d+\*\*\s+\[review claim\]|\Z)',
        re.DOTALL | re.MULTILINE,
    )

    seen = set()
    for section, _start_line in sections:
        for match in legacy_pattern.finditer(section):
            r_id = match.group(1)
            if r_id in seen:
                continue
            seen.add(r_id)

            primary_target = _primary_target(match.group(2), r_id)
            concept = match.group(3).strip()
            body = match.group(4).strip()

            # Extract supported_by from italic line: *R1 is supported by C1, C2, C3.*
            sup_match = re.search(r'\*R\d+\s+is supported by\s+([^*]+)\*', body)
            supported_by = _parse_supported_by(sup_match.group(1)) if sup_match else []
            statement = _clean_review_statement(body)

            directives.append(ReviewDirective(
                r_id=r_id,
                target_doc=primary_target,
                concept=concept,
                statement=statement,
                supported_by=supported_by,
            ))

        for match in v2_pattern.finditer(section):
            r_id = match.group(1)
            if r_id in seen:
                continue

            body = match.group(2).strip()
            target_match = re.search(r'\[target:\s*([^\]]+)\]', body)
            concept_match = re.search(r'\[concept:\s*([^\]]+)\]', body)
            if not target_match or not concept_match:
                continue

            seen.add(r_id)
            support_match = re.search(r'^\s+supported_by:\s*(.+)$', body, re.MULTILINE)
            supported_by = _parse_supported_by(support_match.group(1)) if support_match else []
            statement = _clean_review_statement(body)

            directives.append(ReviewDirective(
                r_id=r_id,
                target_doc=_primary_target(target_match.group(1), r_id),
                concept=concept_match.group(1).strip(),
                statement=statement,
                supported_by=supported_by,
            ))

    return directives


def parse_bridge_note(path: Path, project_key: str) -> BridgeNote:
    """Parse a bridge note file into structured data."""
    text = path.read_text()
    fm = parse_frontmatter(text)

    return BridgeNote(
        path=path,
        doc_id=fm.get("doc_id", ""),
        doc_kind=fm.get("doc_kind", "research"),
        status=fm.get("status", "draft"),
        disposition=fm.get("disposition", ""),
        research_subkind=fm.get("research_subkind", ""),
        concepts=fm.get("concepts") or [],
        depends_on=fm.get("depends_on") or [],
        relates_to=fm.get("relates_to") or [],
        claims=parse_claims(text),
        questions=parse_questions(text),
        review_directives=parse_review_directives(text),
        project_key=project_key,
    )


def _iter_raw_review_blocks(text: str) -> list[dict]:
    """Return raw R-claim-like blocks from review-capable sections."""
    blocks = []
    pattern = re.compile(
        r'(?ms)^(?:-\s*)?\*\*(R\d+)\*\*.*?'
        r'(?=^(?:-\s*)?\*\*R\d+\*\*|\Z)'
    )
    for section, start_line in _extract_review_sections(text):
        for match in pattern.finditer(section):
            line = start_line + section.count("\n", 0, match.start())
            blocks.append({
                "r_id": match.group(1),
                "line": line,
                "text": match.group(0).strip(),
            })
    return blocks


def build_parse_report(note: BridgeNote, text: str) -> ParseReport:
    """Build a parse-only validation report for a bridge note."""
    issues: list[ParseIssue] = []
    c_ids = [claim.c_id for claim in note.claims]
    c_id_set = set(c_ids)
    r_ids = [directive.r_id for directive in note.review_directives]
    parsed_r_ids = set(r_ids)
    raw_blocks = _iter_raw_review_blocks(text)
    raw_r_ids = [block["r_id"] for block in raw_blocks]

    for claim in note.claims:
        if re.search(r'(?:^|\s)(?:-\s*)?\*\*R\d+\*\*', claim.statement):
            issues.append(ParseIssue(
                "error",
                "claim_contains_review_directive",
                f"{claim.c_id} statement appears to include R-claim text",
            ))

    for block in raw_blocks:
        r_id = block["r_id"]
        body = block["text"]
        if not re.search(r'\[target:\s*[^\]]+\]', body):
            issues.append(ParseIssue(
                "error", "missing_target",
                f"{r_id} is missing [target: ...]",
                block["line"],
            ))
        if not re.search(r'\[concept:\s*[^\]]+\]', body):
            issues.append(ParseIssue(
                "error", "missing_concept",
                f"{r_id} is missing [concept: ...]",
                block["line"],
            ))
        if not re.search(r'\bsupported_by:\s*|\*R\d+\s+is supported by\s+', body):
            issues.append(ParseIssue(
                "warning", "missing_supported_by",
                f"{r_id} has no supported_by line",
                block["line"],
            ))
        if r_id not in parsed_r_ids and re.search(r'\[target:\s*[^\]]+\]', body) and re.search(r'\[concept:\s*[^\]]+\]', body):
            issues.append(ParseIssue(
                "error", "unparsed_review_directive",
                f"{r_id} has target and concept metadata but was not parsed",
                block["line"],
            ))

    for directive in note.review_directives:
        if not directive.supported_by:
            issues.append(ParseIssue(
                "warning", "missing_supported_by",
                f"{directive.r_id} parsed with no source-claim support refs",
            ))
        for c_id in directive.supported_by:
            if c_id not in c_id_set:
                issues.append(ParseIssue(
                    "error", "unknown_support_ref",
                    f"{directive.r_id} supported_by references unknown {c_id}",
                ))

    return ParseReport(
        path=note.path,
        doc_id=note.doc_id,
        project_key=note.project_key,
        c_ids=c_ids,
        r_ids=r_ids,
        raw_r_ids=raw_r_ids,
        issues=issues,
    )


def format_parse_report(report: ParseReport) -> str:
    """Format a parse report for CLI output."""
    lines = [
        f"Parse report: {report.path}",
        f"  doc_id: {report.doc_id or '(missing)'}",
        f"  project: {report.project_key}",
        f"  C-claims parsed: {len(report.c_ids)} {report.c_ids}",
        f"  R-claims parsed: {len(report.r_ids)} {report.r_ids}",
        f"  Raw R-like blocks: {len(report.raw_r_ids)} {report.raw_r_ids}",
    ]
    if report.issues:
        lines.append("  Issues:")
        for issue in report.issues:
            loc = f" line {issue.line}:" if issue.line is not None else ":"
            lines.append(f"    {issue.severity.upper()} {issue.code}{loc} {issue.message}")
    else:
        lines.append("  Issues: none")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entity URI generation (mirrors KOI's generate_entity_uri)
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normalize entity text for matching."""
    return text.lower().strip()

def generate_entity_uri(name: str, entity_type: str) -> str:
    """Generate a deterministic URI matching KOI's convention."""
    normalized = normalize_text(name)
    hash_input = f"{entity_type}:{normalized}"
    hash_id = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
    type_prefix = entity_type.lower()
    safe_name = normalized.replace(' ', '-').replace("'", '')[:50]
    return f"orn:personal-koi.entity:{type_prefix}-{safe_name}-{hash_id}"


# ---------------------------------------------------------------------------
# KOI interactions
# ---------------------------------------------------------------------------

async def resolve_project_uri(conn: asyncpg.Connection, project_id: str) -> str:
    """Resolve project URI from KOI. Fail closed if not exactly 1 row."""
    rows = await conn.fetch(
        "SELECT fuseki_uri FROM entity_registry "
        "WHERE entity_type = 'Project' AND metadata->>'project_id' = $1",
        project_id,
    )
    if len(rows) == 0:
        raise RuntimeError(f"Project entity not found for project_id='{project_id}'. "
                           "Run ingest_spec_dag.py first.")
    if len(rows) > 1:
        uris = [r["fuseki_uri"] for r in rows]
        raise RuntimeError(f"Ambiguous project for project_id='{project_id}': {uris}")
    return rows[0]["fuseki_uri"]


async def resolve_or_create_concept(
    conn: asyncpg.Connection,
    concept_name: str,
) -> str:
    """Resolve a concept name to an entity URI, creating if needed."""
    # Try exact match first
    normalized = normalize_text(concept_name)
    row = await conn.fetchrow(
        "SELECT fuseki_uri FROM entity_registry "
        "WHERE normalized_text = $1 AND entity_type = 'Concept' LIMIT 1",
        normalized,
    )
    if row:
        return row["fuseki_uri"]

    # Also try with hyphens replaced by spaces (concept tags use hyphens)
    normalized_spaced = normalized.replace("-", " ")
    if normalized_spaced != normalized:
        row = await conn.fetchrow(
            "SELECT fuseki_uri FROM entity_registry "
            "WHERE normalized_text = $1 AND entity_type = 'Concept' LIMIT 1",
            normalized_spaced,
        )
        if row:
            return row["fuseki_uri"]

    # Create new concept entity
    uri = generate_entity_uri(concept_name.replace("-", " "), "Concept")
    entity_text = concept_name.replace("-", " ").title()

    await conn.execute(
        "INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text, metadata) "
        "VALUES ($1, $2, 'Concept', $3, $4::jsonb) "
        "ON CONFLICT (fuseki_uri) DO NOTHING",
        uri, entity_text, normalized_spaced or normalized,
        json.dumps({"source": "learning_field"}),
    )
    log.info(f"  Created Concept: {entity_text} → {uri}")
    return uri


async def find_previous_source_claim(
    conn: asyncpg.Connection,
    source_document: str,
    c_id: str,
) -> Optional[dict]:
    """Find the most recent source claim for a (doc, C-ID) pair."""
    row = await conn.fetchrow(
        "SELECT claim_rid, entity_uri, statement, "
        "       metadata->>'evidence_anchor' AS evidence_anchor, "
        "       metadata->>'confidence' AS confidence, "
        "       metadata->>'project_uri' AS project_uri "
        "FROM claims "
        "WHERE source_document = $1 "
        "  AND metadata->>'c_id' = $2 "
        "  AND metadata->>'source' = 'learning_field' "
        "  AND metadata->>'claim_layer' = 'source' "
        "ORDER BY created_at DESC LIMIT 1",
        source_document, c_id,
    )
    if row:
        return {
            "claim_rid": row["claim_rid"],
            "entity_uri": row["entity_uri"],
            "statement": row["statement"],
            "evidence_anchor": row["evidence_anchor"],
            "confidence": row["confidence"],
            "project_uri": row["project_uri"],
        }
    return None


async def create_source_claim(
    client: httpx.AsyncClient,
    conn: asyncpg.Connection,
    *,
    claimant_uri: str,
    statement: str,
    about_uri: str,
    source_document: str,
    c_id: str,
    confidence: str,
    anchor: str,
    project_uri: str,
    projection_batch: str,
) -> dict:
    """Create a source claim via POST /claims/, with versioning."""
    # Check for previous version of this claim
    previous = await find_previous_source_claim(conn, source_document, c_id)
    supersedes_rid = None
    if previous:
        business_key_unchanged = (
            previous["statement"] == statement
            and previous["evidence_anchor"] == anchor
            and previous["confidence"] == confidence
            and previous["project_uri"] == project_uri
        )
        if business_key_unchanged:
            # True no-op: server-side claim_rid would be identical, but skip the
            # round-trip so the projection summary reflects zero new claims.
            log.info(f"  Source claim {c_id}: unchanged, skipping (dedup early-return)")
            # Refresh projection_batch so the audit trail records this run.
            await conn.execute(
                "UPDATE claims SET metadata = metadata || $1::jsonb WHERE claim_rid = $2",
                json.dumps({"projection_batch": projection_batch}),
                previous["claim_rid"],
            )
            return {
                "claim_rid": previous["claim_rid"],
                "entity_uri": previous["entity_uri"],
                "_dedup_skipped": True,
            }
        if previous["statement"] != statement:
            supersedes_rid = previous["claim_rid"]
            log.info(f"  {c_id} supersedes {supersedes_rid} (statement changed)")

    payload = {
        "claimant_uri": claimant_uri,
        "statement": statement,
        "claim_type": "governance",
        "about_uri": about_uri,
        "source_document": source_document,
        "metadata": {
            "c_id": c_id,
            "confidence": confidence,
            "evidence_anchor": anchor,
            "claim_layer": "source",
            "extraction_status": "extracted",
            "project_uri": project_uri,
            "source": "learning_field",
        },
        "created_by": "darren",
    }
    if supersedes_rid:
        payload["supersedes_rid"] = supersedes_rid

    resp = await client.post(f"{KOI_BASE}/claims/", json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"/claims auth failed ({resp.status_code}) while creating source claim {c_id}; "
                "token missing or rejected."
            )
        raise RuntimeError(
            f"Failed to create source claim {c_id}: {resp.status_code} "
            f"{_http_error_snippet(resp.text)}"
        )
    data = resp.json()
    claim_rid = data.get("claim_rid", "?")
    log.info(f"  Source claim {c_id}: {claim_rid}")

    # Add projection_batch to metadata (post-creation, doesn't affect RID)
    await conn.execute(
        "UPDATE claims SET metadata = metadata || $1::jsonb WHERE claim_rid = $2",
        json.dumps({"projection_batch": projection_batch}),
        claim_rid,
    )
    return data


async def create_review_claim(
    client: httpx.AsyncClient,
    conn: asyncpg.Connection,
    *,
    claimant_uri: str,
    concept_name: str,
    about_uri: str,
    target_spec_doc: str,
    disposition_slug: str,
    project_uri: str,
    projection_batch: str,
) -> dict:
    """Create a review claim via POST /claims/. Deterministic from target+concept.

    Note: review claims intentionally leave the top-level ``source_document``
    column empty. They are governance-layer recommendations keyed by
    (target_spec_doc, concept, disposition) so that multiple bridge notes
    converge on the same claim RID rather than each spawning a duplicate.
    Provenance back to the authoring bridge note is preserved via the
    supporting source (C-) claims, which do carry source_document and are
    linked to the review claim by ``supports`` edges in entity_relationships.

    Consumers filtering by ``source_document = '<bridge-note-doc-id>'`` will
    therefore only see source claims; to enumerate the review claims induced
    by a bridge note, traverse ``supports`` edges from its source claims
    (or filter on ``metadata->>'projection_batch'`` for batch-level audits).
    """
    change_slug = f"{disposition_slug}-{concept_name}"
    target_spec_uri = f"spec:{target_spec_doc}"
    governance_cluster_key = f"{target_spec_doc}:{concept_name}"
    statement = (
        f"Canon review: {disposition_slug.replace('-', ' ')} — "
        f"{concept_name.replace('-', ' ')} in {target_spec_doc}"
    )

    payload = {
        "claimant_uri": claimant_uri,
        "statement": statement,
        "claim_type": "governance",
        "about_uri": about_uri,
        "metadata": {
            "claim_layer": "review",
            "target_spec_doc": target_spec_doc,
            "target_section": concept_name,
            "change_slug": change_slug,
            "target_spec_uri": target_spec_uri,
            "governance_cluster_key": governance_cluster_key,
            "project_uri": project_uri,
            "source": "learning_field",
        },
        "created_by": "darren",
    }

    resp = await client.post(f"{KOI_BASE}/claims/", json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"/claims auth failed ({resp.status_code}) while creating review claim "
                f"({target_spec_doc} x {concept_name}); token missing or rejected."
            )
        raise RuntimeError(
            f"Failed to create review claim ({target_spec_doc} x {concept_name}): "
            f"{resp.status_code} {_http_error_snippet(resp.text)}"
        )
    data = resp.json()
    claim_rid = data.get("claim_rid", "?")
    log.info(f"  Review claim ({target_spec_doc} × {concept_name}): {claim_rid}")

    # Add projection_batch to metadata (post-creation, doesn't affect RID)
    await conn.execute(
        "UPDATE claims SET metadata = metadata || $1::jsonb WHERE claim_rid = $2",
        json.dumps({"projection_batch": projection_batch}),
        claim_rid,
    )
    return data


async def find_review_claim(
    conn: asyncpg.Connection,
    target_spec_doc: str,
    concept_name: str,
) -> Optional[str]:
    """Find an existing review claim by target + concept in metadata."""
    row = await conn.fetchrow(
        "SELECT entity_uri FROM claims "
        "WHERE metadata->>'claim_layer' = 'review' "
        "  AND metadata->>'source' = 'learning_field' "
        "  AND metadata->>'target_spec_doc' = $1 "
        "  AND metadata->>'governance_cluster_key' = $2 "
        "LIMIT 1",
        target_spec_doc,
        f"{target_spec_doc}:{concept_name}",
    )
    return row["entity_uri"] if row else None


async def insert_edge(
    conn: asyncpg.Connection,
    subject_uri: str,
    predicate: str,
    object_uri: str,
    source: str = "learning_field",
    confidence: float = 1.0,
    source_rid: Optional[str] = None,
) -> bool:
    """Insert an edge into entity_relationships. Returns True if inserted."""
    try:
        result = await conn.execute(
            "INSERT INTO entity_relationships "
            "(subject_uri, predicate, object_uri, confidence, source, source_rid) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT DO NOTHING",
            subject_uri, predicate, object_uri, confidence, source, source_rid,
        )
        return result == "INSERT 0 1"
    except Exception as e:
        log.error(f"  Edge insert failed ({subject_uri} -{predicate}-> {object_uri}): {e}")
        return False


async def create_question_entity(
    conn: asyncpg.Connection,
    question: OpenQuestion,
    doc_id: str,
    source_rid: str,
) -> str:
    """Create a Question entity in entity_registry."""
    # Stable URI from doc_id + question number
    uri = generate_entity_uri(f"{doc_id}-Q{question.number}", "Question")
    entity_text = question.question[:200]

    await conn.execute(
        "INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text, metadata) "
        "VALUES ($1, $2, 'Question', $3, $4::jsonb) "
        "ON CONFLICT (fuseki_uri) DO NOTHING",
        uri, entity_text, normalize_text(entity_text),
        json.dumps({
            "source": "learning_field",
            "source_document": doc_id,
            "question_number": question.number,
        }),
    )
    return uri


# ---------------------------------------------------------------------------
# Projection logic
# ---------------------------------------------------------------------------

async def project_bridge_note(
    note: BridgeNote,
    conn: asyncpg.Connection,
    client: httpx.AsyncClient,
    dry_run: bool = False,
    projection_batch: str = "",
) -> dict:
    """Project a single bridge note into the KOI graph."""
    stats = {
        "source_claims": 0,
        "source_claims_skipped": 0,
        "review_claims": 0,
        "concepts": 0,
        "questions": 0,
        "supports_edges": 0,
        "opposes_edges": 0,
        "about_edges": 0,
        "related_to_edges": 0,
    }

    project_cfg = PROJECTS[note.project_key]
    claimant_uri = project_cfg["claimant_uri"]

    # Disposition determines stance: "no change" → opposes, everything else → supports
    proposes_change = note.disposition != "no change"
    default_stance = "supports" if proposes_change else "opposes"

    has_review_directives = len(note.review_directives) > 0

    log.info(f"\n{'='*60}")
    log.info(f"Projecting: {note.doc_id} ({len(note.claims)} claims, {len(note.questions)} questions)")
    log.info(f"  Disposition: {note.disposition} → stance: {default_stance}")
    log.info(f"  Concepts: {note.concepts}")
    if has_review_directives:
        log.info(f"  Review directives: {len(note.review_directives)} (authoritative)")
    else:
        log.info(f"  Review directives: none (using depends_on fallback)")

    if dry_run:
        log.info("  [DRY RUN] Would create entities and edges")
        stats["source_claims"] = len(note.claims)
        stats["review_claims"] = len(note.review_directives) if has_review_directives else 0
        stats["questions"] = len(note.questions)
        stats["concepts"] = len(note.concepts)
        return stats

    # 1. Resolve project URI (fail-closed)
    project_uri = await resolve_project_uri(conn, project_cfg["project_id"])
    log.info(f"  Project URI: {project_uri}")

    # 2. Resolve/create all concept entities (frontmatter + R-claim concepts)
    concept_uris = {}
    all_concept_names = set(note.concepts)
    for rd in note.review_directives:
        all_concept_names.add(rd.concept)
    for concept_name in all_concept_names:
        uri = await resolve_or_create_concept(conn, concept_name)
        concept_uris[concept_name] = uri
        stats["concepts"] += 1

    # 3. Determine disposition slug
    disp_slug = DISPOSITION_SLUG.get(note.disposition, "unclassified")

    # 4. Determine canon targets (Fix 3: depends_on only for fallback)
    # Only used when note has no explicit review directives.
    fallback_targets = [
        r for r in note.depends_on
        if not r.startswith(note.doc_id)
        and ".connection." not in r
        and ".term." not in r
    ]
    seen = set()
    fallback_targets = [x for x in fallback_targets if not (x in seen or seen.add(x))]

    # 5a. If note has review directives (Fix 1): create review claims from
    # explicit R-claims only. Each R-claim has a deterministic (target, concept).
    # Global dedup (Fix 2): always check for existing review claim first.
    review_claim_cache = {}  # (target, concept) → entity_uri
    source_claim_uris = {}   # c_id → entity_uri (for supported_by linkage)

    if has_review_directives:
        for rd in note.review_directives:
            cache_key = (rd.target_doc, rd.concept)
            if cache_key not in review_claim_cache:
                about_uri = concept_uris.get(rd.concept)
                if not about_uri:
                    log.warning(f"  Skipping {rd.r_id}: concept '{rd.concept}' not resolved")
                    continue
                # Fix 2: always check for existing review claim first
                existing = await find_review_claim(conn, rd.target_doc, rd.concept)
                if existing:
                    review_claim_cache[cache_key] = existing
                    log.info(f"  {rd.r_id}: reusing existing review claim ({rd.target_doc} × {rd.concept})")
                elif proposes_change:
                    review_data = await create_review_claim(
                        client, conn,
                        claimant_uri=claimant_uri,
                        concept_name=rd.concept,
                        about_uri=about_uri,
                        target_spec_doc=rd.target_doc,
                        disposition_slug=disp_slug,
                        project_uri=project_uri,
                        projection_batch=projection_batch,
                    )
                    if review_data and review_data.get("entity_uri"):
                        review_claim_cache[cache_key] = review_data["entity_uri"]
                        stats["review_claims"] += 1

    # 5b. Create source claims + link to review claims
    for claim in note.claims:
        # Determine the primary concept for this claim
        primary_concept = note.concepts[0] if note.concepts else None
        stmt_normalized = claim.statement.lower().replace("-", " ")
        for c in note.concepts:
            c_readable = c.replace("-", " ")
            if c_readable.lower() in stmt_normalized:
                primary_concept = c
                break

        about_uri = concept_uris.get(primary_concept) if primary_concept else None
        if not about_uri:
            log.warning(f"  Skipping claim {claim.c_id}: no concept to link")
            continue

        source_data = await create_source_claim(
            client, conn,
            claimant_uri=claimant_uri,
            statement=claim.statement,
            about_uri=about_uri,
            source_document=note.doc_id,
            c_id=claim.c_id,
            confidence=claim.confidence,
            anchor=claim.anchor,
            project_uri=project_uri,
            projection_batch=projection_batch,
        )

        if not source_data or not source_data.get("entity_uri"):
            continue

        if source_data.get("_dedup_skipped"):
            stats["source_claims_skipped"] += 1
        else:
            stats["source_claims"] += 1
        source_entity_uri = source_data["entity_uri"]
        source_claim_uris[claim.c_id] = source_entity_uri

        if has_review_directives:
            # Link source claims to review claims via supported_by from R-directives
            for rd in note.review_directives:
                if claim.c_id in rd.supported_by:
                    review_uri = review_claim_cache.get((rd.target_doc, rd.concept))
                    if review_uri:
                        inserted = await insert_edge(
                            conn, source_entity_uri, default_stance, review_uri,
                            source_rid=f"projection:{note.doc_id}",
                        )
                        if inserted:
                            if default_stance == "supports":
                                stats["supports_edges"] += 1
                            else:
                                stats["opposes_edges"] += 1
        else:
            # Fallback (Fix 3): use depends_on targets only
            if not fallback_targets or not primary_concept:
                continue

            for target in fallback_targets:
                cache_key = (target, primary_concept)

                if cache_key not in review_claim_cache:
                    # Fix 2: always check for existing review claim first
                    existing = await find_review_claim(conn, target, primary_concept)
                    if existing:
                        review_claim_cache[cache_key] = existing
                    elif proposes_change:
                        review_data = await create_review_claim(
                            client, conn,
                            claimant_uri=claimant_uri,
                            concept_name=primary_concept,
                            about_uri=about_uri,
                            target_spec_doc=target,
                            disposition_slug=disp_slug,
                            project_uri=project_uri,
                            projection_batch=projection_batch,
                        )
                        if review_data and review_data.get("entity_uri"):
                            review_claim_cache[cache_key] = review_data["entity_uri"]
                            stats["review_claims"] += 1

                review_entity_uri = review_claim_cache.get(cache_key)
                if review_entity_uri:
                    inserted = await insert_edge(
                        conn, source_entity_uri, default_stance, review_entity_uri,
                        source_rid=f"projection:{note.doc_id}",
                    )
                    if inserted:
                        if default_stance == "supports":
                            stats["supports_edges"] += 1
                        else:
                            stats["opposes_edges"] += 1

    # 7. Create Question entities + about edges
    for q in note.questions:
        q_uri = await create_question_entity(conn, q, note.doc_id,
                                              source_rid=f"projection:{note.doc_id}")
        stats["questions"] += 1

        # Link question to the most relevant concept
        # Normalize both sides for matching
        linked_concept = note.concepts[0] if note.concepts else None
        q_normalized = q.question.lower().replace("-", " ")
        for c in note.concepts:
            c_readable = c.replace("-", " ")
            if c_readable.lower() in q_normalized:
                linked_concept = c
                break

        if linked_concept and linked_concept in concept_uris:
            inserted = await insert_edge(
                conn, q_uri, "about", concept_uris[linked_concept],
                source_rid=f"projection:{note.doc_id}",
            )
            if inserted:
                stats["about_edges"] += 1

    # 8. Create related_to edges between bridge note SpecDocs (lateral links)
    # The bridge note itself should be a SpecDoc; link to other bridge notes in relates_to
    note_spec_uri = f"spec:{note.doc_id}"
    # Check if this SpecDoc exists
    note_spec_exists = await conn.fetchval(
        "SELECT 1 FROM entity_registry WHERE fuseki_uri = $1", note_spec_uri,
    )

    if note_spec_exists:
        for related_doc_id in note.relates_to:
            related_uri = f"spec:{related_doc_id}"
            related_exists = await conn.fetchval(
                "SELECT 1 FROM entity_registry WHERE fuseki_uri = $1", related_uri,
            )
            if related_exists:
                inserted = await insert_edge(
                    conn, note_spec_uri, "related_to", related_uri,
                    source_rid=f"projection:{note.doc_id}",
                )
                if inserted:
                    stats["related_to_edges"] += 1

    return stats


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_bridge_notes() -> list[tuple[Path, str]]:
    """Find all bridge notes across configured project repos."""
    notes = []

    for project_key, cfg in PROJECTS.items():
        bridge_dir = cfg["bridge_dir"]
        if not bridge_dir.exists():
            log.warning(f"Bridge dir not found: {bridge_dir}")
            continue

        for md_path in sorted(bridge_dir.rglob("*.md")):
            if md_path.name == "CLAUDE.md":
                continue
            # Parse frontmatter rather than substring-match the body —
            # schema docs in YAML code fences can otherwise trigger false positives.
            try:
                text = md_path.read_text()
            except Exception:
                continue
            try:
                fm = parse_frontmatter(text)
            except ValueError:
                continue  # no frontmatter — not a bridge note
            except Exception:
                continue
            if isinstance(fm, dict) and fm.get("research_subkind") == "bridge_note":
                notes.append((md_path, project_key))

    return notes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Project bridge notes into KOI graph")
    parser.add_argument("--parse-report", action="store_true", help="Parse notes and report issues without DB access")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--apply", action="store_true", help="Write to KOI graph")
    parser.add_argument("--note", type=str, help="Project a single note by path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if not args.parse_report and not args.dry_run and not args.apply:
        parser.error("Specify --parse-report, --dry-run, or --apply")

    if args.verbose:
        log.setLevel(logging.DEBUG)

    # Discover notes
    if args.note:
        note_path = Path(args.note).expanduser().resolve()
        # Determine project key from path
        if "intelligence-commons" in str(note_path):
            project_key = "ic"
        elif "flowcoding" in str(note_path):
            project_key = "fc"
        elif "poietic-match" in str(note_path):
            project_key = "pm"
        elif "bioregional-coordination" in str(note_path):
            project_key = "bioregional-coordination"
        elif "bioregional-mapping" in str(note_path):
            project_key = "bioregional-mapping"
        elif "bioregional-economics" in str(note_path):
            project_key = "bioregional-economics"
        else:
            project_key = "spore"
        note_paths = [(note_path, project_key)]
    else:
        note_paths = discover_bridge_notes()

    log.info(f"Found {len(note_paths)} bridge notes")

    if not note_paths:
        log.error("No bridge notes found")
        sys.exit(1)

    if args.parse_report:
        had_errors = False
        for note_path, project_key in note_paths:
            try:
                text = note_path.read_text()
                note = parse_bridge_note(note_path, project_key)
                report = build_parse_report(note, text)
                print(format_parse_report(report))
                if any(issue.severity == "error" for issue in report.issues):
                    had_errors = True
            except Exception as e:
                had_errors = True
                print(f"Parse report: {note_path}")
                print(f"  ERROR parse_failed: {e}")
        sys.exit(1 if had_errors else 0)

    if asyncpg is None or httpx is None:
        parser.error("asyncpg and httpx are required for --dry-run/--apply; use --parse-report for parser-only checks")

    # Parse and validate all notes before touching KOI. This prevents malformed
    # review-claim syntax from silently falling back to source-claim-only behavior.
    parsed_notes = []
    had_parse_errors = False
    for note_path, project_key in note_paths:
        try:
            text = note_path.read_text()
            note = parse_bridge_note(note_path, project_key)
            report = build_parse_report(note, text)
            if any(issue.severity == "error" for issue in report.issues):
                had_parse_errors = True
                log.error("\n" + format_parse_report(report))
            parsed_notes.append(note)
        except Exception as e:
            had_parse_errors = True
            log.error(f"Failed to parse {note_path}: {e}")

    if had_parse_errors:
        log.error("Bridge-note parse validation failed; aborting before KOI access")
        sys.exit(1)

    if not parsed_notes:
        log.error("No parseable bridge notes found")
        sys.exit(1)

    # Connect to KOI
    conn = await asyncpg.connect("postgresql://localhost:5432/personal_koi")

    active_project_keys = {project_key for _, project_key in note_paths}

    # Verify claimant orgs exist for projects participating in this run.
    # Optional configured projects may not have KOI seed entities until they
    # first author bridge notes.
    for project_key in sorted(active_project_keys):
        cfg = PROJECTS[project_key]
        exists = await conn.fetchval(
            "SELECT 1 FROM entity_registry WHERE fuseki_uri = $1",
            cfg["claimant_uri"],
        )
        if not exists:
            log.error(f"Claimant entity missing: {cfg['claimant_uri']}")
            sys.exit(1)

    # Verify project URIs resolve for projects participating in this run.
    for project_key in sorted(active_project_keys):
        cfg = PROJECTS[project_key]
        try:
            uri = await resolve_project_uri(conn, cfg["project_id"])
            log.info(f"Project {cfg['project_id']} → {uri}")
        except RuntimeError as e:
            log.error(str(e))
            sys.exit(1)

    totals = {
        "notes": 0,
        "source_claims": 0,
        "source_claims_skipped": 0,
        "review_claims": 0,
        "concepts": 0,
        "questions": 0,
        "supports_edges": 0,
        "opposes_edges": 0,
        "about_edges": 0,
        "related_to_edges": 0,
    }

    # Process in two passes:
    # Pass 1: change-proposing notes (creates review claims + supports edges)
    # Pass 2: "no change" notes (links to existing review claims with opposes)
    change_notes = [n for n in parsed_notes if n.disposition != "no change"]
    nochange_notes = [n for n in parsed_notes if n.disposition == "no change"]
    log.info(f"  Pass 1: {len(change_notes)} change-proposing notes")
    log.info(f"  Pass 2: {len(nochange_notes)} no-change notes (opposes)")

    batch_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log.info(f"  Projection batch: {batch_ts}")

    # Write path on POST /claims/ requires auth (make_service_token_auth).
    # Send the service token from the env or local koi-state file; never
    # hardcode it. Dry-run stays read-only and can run without auth.
    _svc_token = _claims_service_token() if args.apply else None
    if args.apply and not _svc_token:
        log.error(
            "KOI_CLAIMS_SERVICE_TOKEN not found (env or "
            "~/.config/personal-koi/koi-state/claims_service_token); aborting before writes"
        )
        sys.exit(1)
    _auth_headers = {"Authorization": f"Bearer {_svc_token}"} if _svc_token else {}
    async with httpx.AsyncClient(headers=_auth_headers) as client:
        if args.apply:
            await verify_claims_auth(client)
        for note in change_notes + nochange_notes:
            stats = await project_bridge_note(
                note, conn, client,
                dry_run=args.dry_run,
                projection_batch=batch_ts,
            )
            totals["notes"] += 1
            for k, v in stats.items():
                totals[k] = totals.get(k, 0) + v

    await conn.close()

    log.info(f"\n{'='*60}")
    log.info("PROJECTION SUMMARY")
    log.info(f"  Notes processed: {totals['notes']}")
    log.info(f"  Source claims:   {totals['source_claims']} new"
             f"{' (+' + str(totals.get('source_claims_skipped', 0)) + ' unchanged, dedup-skipped)' if totals.get('source_claims_skipped', 0) else ''}")
    log.info(f"  Review claims:   {totals['review_claims']}")
    log.info(f"  Concepts:        {totals['concepts']}")
    log.info(f"  Questions:       {totals['questions']}")
    log.info(f"  Supports edges:  {totals['supports_edges']}")
    log.info(f"  Opposes edges:   {totals.get('opposes_edges', 0)}")
    log.info(f"  About edges:     {totals['about_edges']}")
    log.info(f"  Related_to edges:{totals['related_to_edges']}")


if __name__ == "__main__":
    asyncio.run(main())
