#!/usr/bin/env python3
"""
Personal KOI Ingest API

FastAPI server for ingesting pre-extracted entities from Claude Code.
Runs on port 8351 as part of the personal KOI-net.

This endpoint accepts entities already extracted by Claude (no LLM cost)
and performs:
1. Entity deduplication against the personal knowledge base
2. Canonical URI assignment
3. Storage in PostgreSQL with pgvector embeddings
4. Returns resolved entities with URIs for vault linking

Entity Resolution Tiers:
- Tier 1: Exact match (normalized text, B-Tree index)
- Tier 1.x: Fuzzy string match (Jaro-Winkler similarity)
- Tier 2: Semantic match (BGE embeddings + pgvector HNSW)
- Tier 3: Create new entity with deterministic URI
"""

import json as json_module_global
import os
import re
import asyncio
import asyncpg
import hashlib
import httpx
import unicodedata
from datetime import datetime, timezone
from typing import List, Dict, Any, Literal, Optional, Tuple
from enum import Enum
from dataclasses import dataclass
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import logging
import uuid
from metaphone import doublemetaphone

# Import vault relationship parser
from api.vault_parser import (
    sync_vault_relationships,
    resolve_pending_relationships,
    get_entity_relationships,
    check_relationship_exists,
    SYMMETRIC_PREDICATES,
)

# Import schema loader
from api.entity_schema import (
    get_entity_schemas,
    get_schema_for_type,
    get_schema_version,
    reload_entity_schemas,
    get_first_significant_token,
    get_phonetic_enabled_types,
    EntityTypeConfig,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Personal KOI Ingest API",
    version="1.0.0",
    description="Ingests pre-extracted entities from Claude Code into personal knowledge base"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (demo portal)
from fastapi.staticfiles import StaticFiles
_static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

@app.get("/demo")
async def demo_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/static/demo.html")

# Load capabilities registry
try:
    from api.capabilities import Capabilities
    _caps = Capabilities.from_env()
    logging.getLogger(__name__).info(f"Capabilities loaded (profile={_caps.deployment_profile})")
except ImportError:
    _caps = None
    logging.getLogger(__name__).info("Capabilities registry not available, using legacy startup")

# Mount KOI-net federation router (if enabled)
if os.getenv('KOI_NET_ENABLED', 'false').lower() in ('true', '1', 'yes'):
    try:
        from api.koi_net_router import koi_net_router
        app.include_router(koi_net_router, prefix="/koi-net")
        logging.getLogger(__name__).info("KOI-net federation router mounted")
    except ImportError as e:
        logging.getLogger(__name__).warning(f"KOI-net federation not available: {e}")

# Configuration
DB_URL = os.getenv('POSTGRES_URL', 'postgresql://darrenzal:@localhost:5432/personal_koi')
KOI_MODE = os.getenv('KOI_MODE', 'personal')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')  # kept for /chat LLM endpoint
ENABLE_SEMANTIC_MATCHING = os.getenv('ENABLE_SEMANTIC_MATCHING', 'true').lower() == 'true'
KOI_NET_ENABLED = os.getenv('KOI_NET_ENABLED', 'false').lower() in ('true', '1', 'yes')
TERMINUSDB_ENABLED = os.getenv('TERMINUSDB_ENABLED', 'false').lower() in ('true', '1', 'yes')
QUARTZ_BASE_URL = os.getenv('QUARTZ_BASE_URL', '').rstrip('/')

# Quartz URL generation
QUARTZ_TYPE_PATHS = {
    "Person": "People",
    "Organization": "Organizations",
    "Project": "Projects",
    "Location": "Locations",
    "Concept": "Concepts",
    "Meeting": "Meetings",
    "Practice": "Practices",
    "Pattern": "Patterns",
    "CaseStudy": "CaseStudies",
    "Bioregion": "Bioregions",
    "Protocol": "Protocols",
    "Playbook": "Playbooks",
    "Question": "Questions",
    "Claim": "Claims",
    "Evidence": "Evidence",
    "Commitment": "Commitments",
    "CommitmentPool": "CommitmentPools",
    "CommitmentAction": "CommitmentActions",
    "Source": "Sources",
    "WorkItem": "WorkItems",
    "Milestone": "Milestones",
    "Initiative": "Initiatives",
    "Decision": "Decisions",
    "Risk": "Risks",
    "Metric": "Metrics",
    "Outcome": "Outcomes",
}

def quartz_slug(name: str) -> str:
    """Convert entity name to Quartz-compatible URL slug."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^\w-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")

def quartz_url(entity_type: str, name: str) -> Optional[str]:
    """Build Quartz URL. Returns None for unknown entity types or if base URL not set."""
    if not QUARTZ_BASE_URL:
        return None
    path = QUARTZ_TYPE_PATHS.get(entity_type)
    if path is None:
        return None
    return f"{QUARTZ_BASE_URL}/{path}/{quartz_slug(name)}"

# DEPRECATED: These are now loaded from vault schemas via entity_schema.py
# Kept as fallback comments for reference
# SEMANTIC_THRESHOLDS = loaded from schema.semantic_threshold
# SIMILARITY_THRESHOLDS = loaded from schema.similarity_threshold

# Global connection pool
db_pool: Optional[asyncpg.Pool] = None
openai_client: Optional[Any] = None  # lazy init for /chat LLM calls

from api.embedding_provider import EmbeddingProvider, create_embedding_provider
embedding_provider: Optional[EmbeddingProvider] = None
terminusdb_adapter: Optional[Any] = None  # TerminusDBAdapter instance (lazy init)


# =============================================================================
# Pydantic Models
# =============================================================================

class ExtractedEntity(BaseModel):
    """Entity extracted by Claude Code"""
    name: str
    type: str  # Person, Organization, Location, Project, Concept
    mentions: List[str] = []
    confidence: float = 0.9
    context: Optional[str] = None
    # Per-entity context for resolution (merged with global context)
    associated_people: Optional[List[str]] = None
    associated_organizations: Optional[List[str]] = None


class ExtractedRelationship(BaseModel):
    """Relationship between entities"""
    subject: str
    predicate: str
    object: str
    confidence: float = 0.9


class ResolutionContext(BaseModel):
    """Context for entity resolution disambiguation"""
    associated_people: Optional[List[str]] = None
    project: Optional[str] = None           # Meeting project name for relationship matching
    organizations: Optional[List[str]] = None  # Mentioned organizations for relationship matching
    topics: Optional[List[str]] = None      # Topics for future use
    associated_orgs: Optional[List[str]] = None  # Deprecated: use organizations instead
    source_text: Optional[str] = None  # Reserved for future use


class IngestRequest(BaseModel):
    """Request to ingest extracted entities"""
    document_rid: str  # e.g., "vault:notes/salish-sea-herring"
    content: Optional[str] = None
    entities: List[ExtractedEntity]
    relationships: List[ExtractedRelationship] = []
    source: str = "obsidian-vault"
    context: Optional[ResolutionContext] = None  # For contextual entity resolution


class CanonicalEntity(BaseModel):
    """Resolved canonical entity"""
    name: str
    uri: str
    type: str
    is_new: bool
    merged_with: Optional[str] = None  # If deduplicated
    confidence: float = 1.0


class IngestStats(BaseModel):
    """Stats from ingest operation"""
    entities_processed: int
    new_entities: int
    resolved_entities: int
    relationships_processed: int
    failed_entities: int
    errors: Optional[List[Dict[str, str]]] = None


class IngestResponse(BaseModel):
    """Response from ingest endpoint"""
    success: bool
    canonical_entities: List[CanonicalEntity]
    receipt_rid: str
    stats: IngestStats


class RegisterEntityRequest(BaseModel):
    """Request to register a vault entity"""
    vault_rid: str  # e.g., "orn:obsidian.entity:Notes/Person/clare-attwell"
    vault_path: str  # e.g., "People/Clare Attwell.md"
    entity_type: str  # Person, Organization, etc.
    name: str
    properties: Dict[str, Any] = {}
    frontmatter: Optional[Dict[str, Any]] = None  # YAML frontmatter for relationship extraction
    content_hash: Optional[str] = None
    publication_scope: Optional[str] = "local_graph"  # "local_graph" | "federated"
    visibility_scope: Optional[str] = "public"  # "public" | "node_private"


class RegisterEntityResponse(BaseModel):
    """Response from register-entity endpoint"""
    success: bool
    canonical_uri: str
    is_new: bool
    vault_rid: str
    merged_with: Optional[str] = None
    collision_warning: Optional[str] = None
    koi_rid: Optional[str] = None


class VaultEntityMapping(BaseModel):
    """Mapping between vault RID and canonical entity"""
    vault_rid: str
    vault_path: str
    canonical_uri: str
    entity_type: str
    name: str
    sync_status: str  # linked, local_only, pending_sync, conflict
    content_hash: str
    last_synced: str


class ResolveRequest(BaseModel):
    """Request to resolve an entity with optional context"""
    label: str
    type_hint: Optional[str] = None
    limit: int = 5
    context: Optional[ResolutionContext] = None


# Graph traversal response models

class GraphNode(BaseModel):
    uri: str
    name: Optional[str] = None
    entity_type: Optional[str] = None
    depth: int = 0


class GraphEdge(BaseModel):
    source: str
    target: str
    predicate: str
    confidence: float = 1.0


class NeighborhoodResponse(BaseModel):
    root: str
    max_depth: int
    direction: str
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    node_count: int
    total_nodes_discovered: int
    edge_count: int
    total_edges_discovered: int
    truncated: bool


class PathStep(BaseModel):
    from_uri: str
    from_name: Optional[str] = None
    predicate: str
    direction: str
    to_uri: str
    to_name: Optional[str] = None


class ShortestPathResponse(BaseModel):
    source: str
    target: str
    found: bool
    path_length: Optional[int] = None
    direction: str
    steps: List[PathStep]
    nodes: List[GraphNode]


# =============================================================================
# Embedding Service (provider-agnostic)
# =============================================================================

async def generate_embedding(text: str) -> Optional[List[float]]:
    """Generate embedding using the configured provider."""
    if not embedding_provider or not ENABLE_SEMANTIC_MATCHING:
        return None
    normalized = normalize_entity_text(text)
    return await embedding_provider.embed_or_none(normalized)


# =============================================================================
# Entity Resolution
# =============================================================================

def normalize_entity_text(text: str) -> str:
    """Normalize entity text for comparison"""
    return (
        text.lower()
        .strip()
        .replace('_', ' ')
        .replace('-', ' ')
        .replace('  ', ' ')
        .lstrip('@')
    )


def normalize_alias(alias: Any) -> str:
    """
    Strip [[...]], lowercase, trim for alias matching.

    Handles wikilinks like [[People/Name|Display]] → name
    """
    alias = str(alias)  # Guard against non-string values
    alias = re.sub(r'\[\[([^\]|]+)(\|[^\]]+)?\]\]', r'\1', alias)  # Strip wikilinks
    # Extract just the name part if it's a path
    if '/' in alias:
        alias = alias.rsplit('/', 1)[-1]
    alias = alias.lower().strip()
    return alias


def get_phonetic_code(text: str) -> Optional[str]:
    """
    Get Double Metaphone code for first token of text.

    Uses first token only to handle cases like "Mihal" vs "Mehul Sangham"
    where full-name comparison would fail but first-token matches.
    """
    if not text:
        return None
    first_token = text.split()[0]
    codes = doublemetaphone(first_token)
    return codes[0] if codes[0] else codes[1]  # Primary or secondary


def phonetic_codes_match(code1: Optional[str], code2: Optional[str]) -> bool:
    """Check if two phonetic codes match (both must be non-empty)."""
    return bool(code1 and code2 and code1 == code2)


def jaro_winkler_similarity(s1: str, s2: str) -> float:
    """Calculate Jaro-Winkler similarity between two strings"""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    len1, len2 = len(s1), len(s2)
    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    # Find matches
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)

        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    # Count transpositions
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (
        matches / len1 +
        matches / len2 +
        (matches - transpositions / 2) / matches
    ) / 3

    # Winkler adjustment (common prefix)
    prefix_len = 0
    for i in range(min(4, min(len1, len2))):
        if s1[i] == s2[i]:
            prefix_len += 1
        else:
            break

    return jaro + prefix_len * 0.1 * (1 - jaro)


# DEPRECATED: Similarity thresholds now loaded from schema
# See entity_schema.py get_schema_for_type() for schema-driven thresholds

# Token overlap constants (not type-specific, just thresholds)
MIN_TOKEN_OVERLAP_RATIO = 0.5  # At least 50% of shorter entity's tokens must match
MIN_TOKEN_OVERLAP_COUNT = 2    # At least 2 tokens must match (for 2+ token entities)


def compute_token_overlap(text1: str, text2: str) -> Tuple[float, int]:
    """
    Compute token (word) overlap between two texts.

    Returns: (overlap_ratio, overlap_count)
    - overlap_ratio: proportion of shorter text's tokens found in longer text
    - overlap_count: number of matching tokens
    """
    tokens1 = set(text1.lower().split())
    tokens2 = set(text2.lower().split())

    # Find intersection
    overlap = tokens1 & tokens2
    overlap_count = len(overlap)

    # Compute ratio based on shorter text
    shorter_len = min(len(tokens1), len(tokens2))
    if shorter_len == 0:
        return 0.0, 0

    overlap_ratio = overlap_count / shorter_len
    return overlap_ratio, overlap_count


def passes_token_overlap_check(text1: str, text2: str, entity_type: str) -> bool:
    """
    Check if two texts pass the token overlap requirement.

    For types with require_token_overlap=True in schema:
    - At least MIN_TOKEN_OVERLAP_RATIO of shorter text's tokens match
    - At least MIN_TOKEN_OVERLAP_COUNT tokens match (for multi-word entities)

    Types with require_token_overlap=False bypass multi-word token overlap,
    but single-word entities ALWAYS require JW >= 0.95 to prevent false merges
    like "Microsoft" → "Miro" or "Marie" → "Marianne".
    """
    # Single-word guard applies to ALL types (before schema bypass)
    # This catches short-name false merges regardless of entity type config
    tokens1 = text1.lower().split()
    tokens2 = text2.lower().split()
    if len(tokens1) == 1 or len(tokens2) == 1:
        jw = jaro_winkler_similarity(text1.lower(), text2.lower())
        return jw >= 0.95

    # Get schema-driven config for multi-word token overlap
    schema = get_schema_for_type(entity_type)
    if not schema.require_token_overlap:
        # Even with token overlap bypassed, guard against first-name inflation for
        # 2-token full names (First Last): require the last tokens (family names)
        # to have a minimum JW similarity so "Benjamin Life" ≠ "Benjamin Neal".
        if len(tokens1) == 2 and len(tokens2) == 2:
            last_jw = jaro_winkler_similarity(tokens1[-1], tokens2[-1])
            if last_jw < 0.75:
                return False
        return True  # Schema says bypass multi-word token overlap check

    overlap_ratio, overlap_count = compute_token_overlap(text1, text2)

    # For multi-word entities, require token overlap
    if overlap_ratio < MIN_TOKEN_OVERLAP_RATIO:
        return False

    if overlap_count < MIN_TOKEN_OVERLAP_COUNT:
        return False

    return True


# =============================================================================
# Relationship-Aware Context Relevance
# =============================================================================

class RelevanceSignal(Enum):
    """Signal from relationship-based context relevance check."""
    POSITIVE = "positive"      # Has relevant relationship
    NEGATIVE = "negative"      # Candidate HAS relationships, but NONE are relevant
    UNKNOWN = "unknown"        # Candidate has no relationships (data incomplete)


@dataclass
class RelevanceResult:
    """Result of context relevance check."""
    signal: RelevanceSignal
    score: float
    details: str


# Predicates that connect people to projects (not affiliated_with which is person→org)
PROJECT_RELEVANCE_PREDICATES = ('involves_person', 'founded', 'has_founder', 'attended')


async def resolve_entity_to_uri(
    conn: asyncpg.Connection,
    entity_name: str,
    entity_type: Optional[str] = None
) -> Optional[str]:
    """
    Resolve an entity name to its canonical URI.

    Args:
        conn: Database connection
        entity_name: Entity name to resolve
        entity_type: Optional type filter

    Returns:
        Canonical URI or None if not found
    """
    normalized = normalize_entity_text(entity_name)
    if entity_type:
        return await conn.fetchval("""
            SELECT fuseki_uri FROM entity_registry
            WHERE normalized_text = $1 AND entity_type = $2
            LIMIT 1
        """, normalized, entity_type)
    else:
        return await conn.fetchval("""
            SELECT fuseki_uri FROM entity_registry
            WHERE normalized_text = $1
            LIMIT 1
        """, normalized)


async def check_context_relevance(
    conn: asyncpg.Connection,
    candidate_uri: str,
    context: ResolutionContext
) -> RelevanceResult:
    """
    Check if candidate has relationships relevant to the resolution context.

    Returns:
    - POSITIVE: Candidate is connected to project/orgs (boost score)
    - NEGATIVE: Candidate has relationships, but none are relevant (penalize)
    - UNKNOWN: Candidate has no relationships (no penalty - data incomplete)
    """
    # First, check if candidate has ANY relationships
    has_any_relationships = await conn.fetchval("""
        SELECT EXISTS(
            SELECT 1 FROM entity_relationships
            WHERE subject_uri = $1 OR object_uri = $1
        )
    """, candidate_uri)

    if not has_any_relationships:
        # No relationships = data incomplete, don't penalize
        return RelevanceResult(RelevanceSignal.UNKNOWN, 0.0, "no relationships in DB")

    # Check connection to meeting's project
    if context.project:
        project_uri = await resolve_entity_to_uri(conn, context.project, 'Project')
        if project_uri:
            connected = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM entity_relationships
                    WHERE ((subject_uri = $1 AND object_uri = $2)
                           OR (subject_uri = $2 AND object_uri = $1))
                    AND predicate = ANY($3)
                )
            """, candidate_uri, project_uri, list(PROJECT_RELEVANCE_PREDICATES))
            if connected:
                return RelevanceResult(RelevanceSignal.POSITIVE, 0.3, f"connected to project")

    # Check connection to mentioned organizations
    # Based on actual data format (verified from DB):
    #   - affiliated_with: Person (subj) → Org (obj)
    #   - has_founder: Person (subj) → Org (obj) - from org's founders: field (parser uses 'incoming' direction)
    #   - founded: Person (subj) → Org (obj) - from person's founder: field
    #   - involves_person: Org/Project (subj) → Person (obj)
    orgs = context.organizations or context.associated_orgs or []
    if orgs:
        for org_name in orgs:
            org_uri = await resolve_entity_to_uri(conn, org_name, 'Organization')
            if org_uri:
                connected = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM entity_relationships
                        WHERE (
                            -- person→org predicates (person is subject)
                            (subject_uri = $1 AND predicate IN ('affiliated_with', 'founded', 'has_founder') AND object_uri = $2)
                            -- org→person predicates (person is object)
                            OR (subject_uri = $2 AND predicate = 'involves_person' AND object_uri = $1)
                        )
                    )
                """, candidate_uri, org_uri)
                if connected:
                    return RelevanceResult(RelevanceSignal.POSITIVE, 0.2, f"affiliated with {org_name}")

    # Try 2-hop path for person → org → project chains
    # Path: Person -[has_founder]→ Org -[has_project]→ Project
    # Or: Project -[involves_person]→ Person (direct link to project)
    if context.project:
        project_uri = await resolve_entity_to_uri(conn, context.project, 'Project')
        if project_uri:
            # Check direct involves_person link first
            direct_project = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM entity_relationships
                    WHERE subject_uri = $2 AND predicate = 'involves_person' AND object_uri = $1
                )
            """, candidate_uri, project_uri)

            if direct_project:
                return RelevanceResult(
                    signal=RelevanceSignal.POSITIVE,
                    score=0.25,
                    details=f"member of project {context.project}"
                )

            # 2-hop: Person -[affiliation/founded/has_founder]→ Org -[has_project]→ Project
            # All person→org predicates: person is subject, org is object
            two_hop = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM entity_relationships er1
                    JOIN entity_relationships er2 ON er1.object_uri = er2.subject_uri
                    WHERE er1.subject_uri = $1
                      AND er1.predicate IN ('affiliated_with', 'founded', 'has_founder')
                      AND er2.predicate = 'has_project'
                      AND er2.object_uri = $2
                )
            """, candidate_uri, project_uri)

            if two_hop:
                return RelevanceResult(
                    signal=RelevanceSignal.POSITIVE,
                    score=0.1,
                    details=f"2-hop path via org to {context.project}"
                )

    # Candidate HAS relationships but NONE match context = negative signal
    return RelevanceResult(RelevanceSignal.NEGATIVE, -0.15, "has relationships, none relevant")


async def check_fallback_relevance(
    conn: asyncpg.Connection,
    candidate_uri: str,
    context: ResolutionContext
) -> float:
    """
    Fallback: Use document_entity_links when relationships are sparse.

    If candidate appears in same documents as context entities, that's a weak positive signal.
    """
    if not context.associated_people:
        return 0.0

    # Check if candidate co-occurs with associated people in documents
    people_uris = []
    for person in context.associated_people:
        uri = await resolve_entity_to_uri(conn, person, 'Person')
        if uri:
            people_uris.append(uri)

    if not people_uris:
        return 0.0

    # Count shared documents
    shared_docs = await conn.fetchval("""
        SELECT COUNT(DISTINCT d1.document_rid)
        FROM document_entity_links d1
        JOIN document_entity_links d2 ON d1.document_rid = d2.document_rid
        WHERE d1.entity_uri = $1
        AND d2.entity_uri = ANY($2)
    """, candidate_uri, people_uris)

    if shared_docs and shared_docs > 0:
        return min(shared_docs * 0.05, 0.15)  # Cap at 0.15

    return 0.0


async def resolve_entity(
    conn: asyncpg.Connection,
    entity: ExtractedEntity,
    context: Optional[ResolutionContext] = None
) -> Tuple[CanonicalEntity, bool]:
    """
    Resolve an entity against the knowledge base.

    Resolution Tiers:
    - Tier 1: Exact match (normalized text)
    - Tier 1.5: Contextual co-occurrence match (all entity types with phonetic boost for Person)
    - Tier 2a: Fuzzy match (Jaro-Winkler with token overlap check)
    - Tier 2b: Semantic match (OpenAI embeddings + pgvector)
    - Tier 3: Create new entity

    Args:
        conn: Database connection
        entity: The entity to resolve
        context: Optional disambiguation context (associated_people)

    Returns: (CanonicalEntity, is_new)
    """
    normalized = normalize_entity_text(entity.name)

    # Get schema-driven config for this entity type
    schema = get_schema_for_type(entity.type)
    threshold = schema.similarity_threshold

    # Tier 1: Exact match (normalized text)
    if entity.type:
        exact_match = await conn.fetchrow("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE normalized_text = $1
            AND entity_type = $2
            LIMIT 1
        """, normalized, entity.type)
    else:
        exact_match = await conn.fetchrow("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE normalized_text = $1
            LIMIT 1
        """, normalized)

    if exact_match:
        return CanonicalEntity(
            name=exact_match['entity_text'],
            uri=exact_match['fuseki_uri'],
            type=exact_match['entity_type'] or entity.type,
            is_new=False,
            merged_with=entity.name if exact_match['entity_text'] != entity.name else None,
            confidence=1.0
        ), False

    # Tier 1.1: Alias match (check if input matches any registered alias)
    # Uses normalized name to search against TEXT[] aliases column
    normalized_name = normalize_alias(entity.name)

    if entity.type:
        alias_match = await conn.fetchrow("""
            SELECT fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE entity_type = $1
            AND $2 = ANY(aliases)
            LIMIT 1
        """, entity.type, normalized_name)
    else:
        # Type-agnostic alias lookup (when type_hint not provided)
        # Risk: may return wrong entity if alias is reused across types
        logger.warning(f"Type-agnostic alias lookup for '{entity.name}' - consider providing type_hint")
        alias_match = await conn.fetchrow("""
            SELECT fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE $1 = ANY(aliases)
            LIMIT 1
        """, normalized_name)

    if alias_match:
        # Alias match = Tier-1 exact (short-circuit, don't enter contextual pool)
        logger.info(f"Tier 1.1 alias match: '{entity.name}' → '{alias_match['entity_text']}'")
        return CanonicalEntity(
            name=alias_match["entity_text"],
            uri=alias_match["fuseki_uri"],
            type=alias_match["entity_type"] or entity.type,
            is_new=False,
            merged_with=entity.name if alias_match["entity_text"] != entity.name else None,
            confidence=1.0,
        ), False

    # Tier 1.5: Contextual co-occurrence match (ALL entity types, with phonetic boost)
    # Requirements:
    # - min_context_people from schema (default: Person=1, others=2)
    # - Two-tier threshold:
    #   - With phonetic match: combined_score ≥0.6 (phonetic is strong evidence)
    #   - Without phonetic match: combined_score ≥0.75 (stricter to avoid false positives)
    if context and context.associated_people:
        min_people = schema.min_context_people

        if len(context.associated_people) >= min_people:
            logger.info(f"Tier 1.5: Trying contextual match for '{entity.name}' ({entity.type}) "
                       f"with {len(context.associated_people)} associated people")

            contextual_candidates = await get_contextual_entity_candidates(
                conn,
                entity.name,
                entity.type,
                context.associated_people,
                context  # Pass full context for relationship checking
            )

            if contextual_candidates:
                best = contextual_candidates[0]
                has_phonetic = best.get('phonetic_match', False)

                # Token overlap guard for multi-token names (prevents "Silke Helfrich" -> "Simon Grant")
                # If both names have 2+ tokens and share zero tokens, reject regardless of score
                query_tokens = set(normalized.lower().split())
                candidate_tokens = set(best.get('normalized_text', best['name']).lower().split())
                token_overlap = query_tokens & candidate_tokens
                if len(query_tokens) >= 2 and len(candidate_tokens) >= 2 and len(token_overlap) == 0:
                    logger.info(f"Tier 1.5 contextual match REJECTED (zero token overlap): "
                               f"'{entity.name}' -> '{best['name']}' "
                               f"(tokens: {query_tokens} vs {candidate_tokens})")
                else:
                    # Two-tier threshold: phonetic matches get lower bar (strong evidence)
                    # Non-phonetic matches need higher score to avoid false positives
                    threshold_phonetic = 0.6      # "Quoxala" -> "Kwaxala" (same sound)
                    threshold_no_phonetic = 0.75  # Stricter: avoid "Miranda" -> "Mehul Sangham"

                    effective_threshold = threshold_phonetic if has_phonetic else threshold_no_phonetic

                    if best["combined_score"] >= effective_threshold:
                        logger.info(f"Tier 1.5 contextual match: '{entity.name}' -> '{best['name']}' "
                                   f"(combined_score: {best['combined_score']:.3f}, "
                                   f"phonetic: {has_phonetic}, threshold: {effective_threshold})")
                        return CanonicalEntity(
                            name=best["name"],
                            uri=best["uri"],
                            type=best.get("entity_type") or entity.type,
                            is_new=False,
                            merged_with=entity.name if best["name"] != entity.name else None,
                            confidence=best["combined_score"]  # Always 0-1 scale
                        ), False
                    else:
                        logger.info(f"Tier 1.5 contextual match REJECTED: '{entity.name}' -> '{best['name']}' "
                                   f"(score: {best['combined_score']:.3f} < threshold: {effective_threshold}, "
                                   f"phonetic: {has_phonetic})")

    # Tier 2a: Fuzzy match (Jaro-Winkler with token overlap check)
    if entity.type:
        candidates = await conn.fetch("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE entity_type = $1
        """, entity.type)
    else:
        candidates = await conn.fetch("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
        """)

    best_match = None
    best_score = 0.0

    for candidate in candidates:
        score = jaro_winkler_similarity(normalized, candidate['normalized_text'])
        if score >= threshold and score > best_score:
            cand_norm = candidate['normalized_text']

            # Length ratio guard: reject if candidate is much longer (prefix-match inflation)
            # e.g. "regen ai" (8) vs "regen ai bd sprint scope" (24) → ratio 3.0
            len_shorter = min(len(normalized), len(cand_norm))
            len_longer = max(len(normalized), len(cand_norm))
            if len_shorter > 0 and len_longer / len_shorter > 1.8 and score < 0.95:
                logger.info(f"Fuzzy match REJECTED (length ratio {len_longer/len_shorter:.1f}x): "
                           f"{entity.name} vs {candidate['entity_text']} | JW={score:.3f}")
                continue

            # Additional check: token overlap for Organization/Project/Concept
            overlap_ratio, overlap_count = compute_token_overlap(normalized, cand_norm)
            logger.info(f"Fuzzy candidate: {entity.name} vs {candidate['entity_text']} | JW={score:.3f} | overlap={overlap_count} ({overlap_ratio:.2f})")
            if not passes_token_overlap_check(normalized, cand_norm, entity.type):
                logger.info(f"Fuzzy match REJECTED due to low token overlap: {entity.name} vs {candidate['entity_text']}")
                continue
            best_score = score
            best_match = candidate

    if best_match:
        return CanonicalEntity(
            name=best_match['entity_text'],
            uri=best_match['fuseki_uri'],
            type=best_match['entity_type'] or entity.type,
            is_new=False,
            merged_with=entity.name if best_match['entity_text'] != entity.name else None,
            confidence=best_score
        ), False

    # Tier 2b: Semantic match (embeddings + pgvector)
    if embedding_provider and ENABLE_SEMANTIC_MATCHING:
        embedding = await generate_embedding(entity.name)
        if embedding:
            semantic_threshold = schema.semantic_threshold

            # Query for semantic matches using pgvector cosine similarity
            if entity.type:
                semantic_match = await conn.fetchrow("""
                    SELECT id, fuseki_uri, entity_text, entity_type,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM entity_registry
                    WHERE embedding IS NOT NULL
                      AND entity_type = $2
                      AND 1 - (embedding <=> $1::vector) > $3
                    ORDER BY similarity DESC
                    LIMIT 1
                """, str(embedding), entity.type, semantic_threshold)
            else:
                semantic_match = await conn.fetchrow("""
                    SELECT id, fuseki_uri, entity_text, entity_type,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM entity_registry
                    WHERE embedding IS NOT NULL
                      AND 1 - (embedding <=> $1::vector) > $2
                    ORDER BY similarity DESC
                    LIMIT 1
                """, str(embedding), semantic_threshold)

            if semantic_match:
                logger.info(f"Tier 2b semantic match: '{entity.name}' -> '{semantic_match['entity_text']}' "
                           f"(similarity: {semantic_match['similarity']:.3f})")
                return CanonicalEntity(
                    name=semantic_match['entity_text'],
                    uri=semantic_match['fuseki_uri'],
                    type=semantic_match['entity_type'] or entity.type,
                    is_new=False,
                    merged_with=entity.name if semantic_match['entity_text'] != entity.name else None,
                    confidence=float(semantic_match['similarity'])
                ), False

    # Tier 3: Create new entity
    new_uri = generate_entity_uri(entity.name, entity.type)

    return CanonicalEntity(
        name=entity.name,
        uri=new_uri,
        type=entity.type,
        is_new=True,
        confidence=entity.confidence
    ), True


def generate_entity_uri(name: str, entity_type: str) -> str:
    """Generate a deterministic URI for a new entity"""
    normalized = normalize_entity_text(name)
    # Create a stable hash-based ID
    hash_input = f"{entity_type}:{normalized}"
    hash_id = hashlib.sha256(hash_input.encode()).hexdigest()[:12]

    type_prefix = entity_type.lower()
    safe_name = normalized.replace(' ', '-').replace("'", '')[:50]

    return f"orn:personal-koi.entity:{type_prefix}-{safe_name}-{hash_id}"


async def store_new_entity(
    conn: asyncpg.Connection,
    entity: ExtractedEntity,
    canonical: CanonicalEntity,
    document_rid: str,
    source: str = 'personal-vault'
) -> None:
    """Store a new entity in the registry with embedding and phonetic code"""
    normalized = normalize_entity_text(entity.name)

    import json as json_module
    metadata = json_module.dumps({
        'mentions': entity.mentions,
        'context': entity.context,
        'confidence': entity.confidence
    })

    # Generate embedding for new entity (enables future Tier 2 matching)
    embedding = None
    if embedding_provider and ENABLE_SEMANTIC_MATCHING:
        embedding = await generate_embedding(entity.name)
        if embedding:
            logger.info(f"Generated embedding for new entity: {entity.name}")

    # Compute phonetic code for types with phonetic_matching enabled (schema-driven)
    phonetic_code = None
    schema = get_schema_for_type(entity.type)
    if schema.phonetic_matching:
        # Use first significant token (skip stopwords)
        first_token = get_first_significant_token(normalized, schema.phonetic_stopwords)
        phonetic_code = get_phonetic_code(first_token)
        if phonetic_code:
            logger.info(f"Generated phonetic code for new {entity.type}: {entity.name} -> {phonetic_code}")

    if embedding:
        await conn.execute("""
            INSERT INTO entity_registry (
                fuseki_uri, entity_text, entity_type, normalized_text,
                source, first_seen_rid, metadata, embedding, phonetic_code
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::vector, $9)
            ON CONFLICT (fuseki_uri) DO NOTHING
        """,
            canonical.uri,
            entity.name,
            entity.type,
            normalized,
            source,
            document_rid,
            metadata,
            str(embedding),
            phonetic_code
        )
    else:
        await conn.execute("""
            INSERT INTO entity_registry (
                fuseki_uri, entity_text, entity_type, normalized_text,
                source, first_seen_rid, metadata, phonetic_code
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            ON CONFLICT (fuseki_uri) DO NOTHING
        """,
            canonical.uri,
            entity.name,
            entity.type,
            normalized,
            source,
            document_rid,
            metadata,
            phonetic_code
        )

    # Enqueue entity to TerminusDB outbox (same transaction as PG write)
    await enqueue_outbox(conn, "entity_upsert", {
        "fuseki_uri": canonical.uri,
        "entity_text": entity.name,
        "entity_type": entity.type,
        "normalized_text": normalized,
        "occurrence_count": 0,
        "phonetic_code": phonetic_code or "",
        "aliases": [],
        "created_by": "darren-personal",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "first_seen_rid": document_rid,
    }, rid=canonical.uri, source_rid=document_rid)


# =============================================================================
# TerminusDB Outbox Helpers
# =============================================================================

def _build_assertion_payload(row: dict) -> dict:
    """Build a complete Assertion payload from an entity_relationships row.

    Computes assertion_hash and normalized_object_key so the outbox payload
    matches the full Assertion schema required by TerminusDB.
    """
    from scripts.terminusdb.schema import (
        compute_assertion_hash,
        canonical_object_key,
        serialize_object_key,
    )

    subject = row["subject_uri"]
    predicate = row["predicate"]
    object_uri = row["object_uri"]
    source = row.get("source") or "personal-vault"
    source_rid = row.get("source_rid") or ""
    source_field = row.get("source_field") or ""
    raw_value = row.get("raw_value") or ""
    confidence = float(row.get("confidence") or 1.0)

    ahash = compute_assertion_hash(
        subject_uri=subject,
        predicate=predicate,
        object_kind="entity",
        object_uri=object_uri,
        literal_value="",
        literal_datatype="",
        literal_lang="",
        source=source,
        source_rid=source_rid,
        source_field=source_field,
        asserted_by="darren-personal",
    )

    assertion_dict = {
        "object_kind": "entity",
        "object_uri": object_uri,
        "literal_value": "",
        "literal_datatype": "",
        "literal_lang": "",
    }
    norm_key = serialize_object_key(canonical_object_key(assertion_dict))

    return {
        "assertion_hash": ahash,
        "subject_uri": subject,
        "predicate": predicate,
        "object_kind": "entity",
        "object_uri": object_uri,
        "literal_value": "",
        "literal_datatype": "",
        "literal_lang": "",
        "asserted_by": "darren-personal",
        "asserted_at": datetime.now(timezone.utc).isoformat(),
        "confidence": confidence,
        "source": source,
        "source_rid": source_rid,
        "source_field": source_field,
        "raw_value": raw_value,
        "status": "active",
        "normalized_object_key": norm_key,
    }


async def _enqueue_relationship_outbox(
    conn: asyncpg.Connection,
    entity_uri: str,
    vault_path: str,
) -> None:
    """After sync_vault_relationships, enqueue a retract + upserts for all current relationships."""
    # Retract old assertions from this source file
    await enqueue_outbox(conn, "assertion_retract", {},
                         rid=entity_uri, source_rid=vault_path)

    # Query the actual relationship rows just written by sync_vault_relationships
    rows = await conn.fetch("""
        SELECT subject_uri, predicate, object_uri, confidence,
               source, source_rid, source_field, raw_value
        FROM entity_relationships
        WHERE source_rid = $1
    """, vault_path)

    for row in rows:
        rel_payload = _build_assertion_payload(dict(row))
        await enqueue_outbox(conn, "assertion_upsert", rel_payload,
                             rid=row["subject_uri"], source_rid=vault_path)


async def enqueue_outbox(
    conn: asyncpg.Connection,
    operation: str,
    payload: dict,
    rid: str,
    source_rid: str = "",
) -> bool:
    """Enqueue an operation to the TerminusDB outbox (same transaction as PG write).

    Returns True if enqueued, False if dedup skipped.
    """
    if not TERMINUSDB_ENABLED:
        return False
    payload_json = json_module_global.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(
        f"{operation}:{rid}:{payload_json}".encode()
    ).hexdigest()
    result = await conn.execute("""
        INSERT INTO terminusdb_outbox (operation, payload, payload_hash, rid, source_rid)
        VALUES ($1, $2::jsonb, $3, $4, $5)
        ON CONFLICT (payload_hash) WHERE status IN ('pending', 'processing') DO NOTHING
    """, operation, payload_json, payload_hash, rid, source_rid)
    return "INSERT" in result


# =============================================================================
# API Endpoints
# =============================================================================

@app.on_event("startup")
async def startup():
    """Initialize database connection pool and embedding provider"""
    global db_pool, openai_client, embedding_provider
    try:
        db_pool = await asyncpg.create_pool(
            DB_URL,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
        logger.info(f"Connected to database (mode: {KOI_MODE})")

        # 1. Init embedding provider (reads env vars only, no API call)
        embedding_provider = create_embedding_provider()

        # 2. Dimension guard: check provider dim matches existing tables
        if embedding_provider:
            async with db_pool.acquire() as conn:
                for table in ['entity_registry', 'session_chunks']:
                    regclass = await conn.fetchval(
                        "SELECT to_regclass($1)", table
                    )
                    if regclass is None:
                        continue  # table doesn't exist yet (fresh install)
                    existing_dim = await conn.fetchval("""
                        SELECT atttypmod FROM pg_attribute
                        WHERE attrelid = to_regclass($1)
                        AND attname = 'embedding' AND atttypmod > 0
                    """, table)
                    if existing_dim is not None and existing_dim > 0 and existing_dim != embedding_provider.dimension:
                        logger.fatal(
                            f"DIMENSION MISMATCH: provider outputs {embedding_provider.dimension}-dim "
                            f"but {table}.embedding is vector({existing_dim}). "
                            f"Cannot start. Re-embed data or change EMBEDDING_PROVIDER."
                        )
                        raise SystemExit(1)
            logger.info(f"Tier 2 semantic matching: ENABLED")
        else:
            logger.info("Tier 2 semantic matching: DISABLED (no embedding provider)")

        # 3. Ensure schema (uses provider dimension for new tables)
        dim = embedding_provider.dimension if embedding_provider else 1536
        async with db_pool.acquire() as conn:
            await ensure_schema(conn, embedding_dim=dim)

        # Verify graph traversal indexes
        async with db_pool.acquire() as conn:
            from api.graph_queries import verify_indexes
            await verify_indexes(conn)

        # Mount capability-gated routers (after pool init)
        if _caps is not None:
            # NOTE: graph_router is NOT mounted here because /graph/neighborhood
            # and /graph/shortest-path are already defined inline on app (lines 3755, 3805).
            # Mounting the router would create duplicate routes. The inline endpoints
            # will be removed in a future phase when the router is fully validated.
            # The router adds temporal endpoints (/graph/history, /graph/timeline) that
            # don't overlap — mount only those when assertion_history is enabled.
            if _caps.graph_queries and _caps.assertion_history:
                try:
                    from api.routers.graph_router import create_temporal_router
                    app.include_router(create_temporal_router(db_pool, _caps))
                    logger.info("Graph temporal router mounted (/graph/history, /graph/timeline)")
                except Exception as e:
                    logger.warning(f"Graph temporal router not mounted: {e}")

            if _caps.web_sensor:
                try:
                    from api.routers.web_router import create_router as create_web_router
                    app.include_router(create_web_router(db_pool, _caps))
                    logger.info("Web router mounted")
                except Exception as e:
                    logger.warning(f"Web router not mounted: {e}")

            # Commitment pooling router (always on, no capability gate)
            try:
                from api.routers.commitment_router import create_router as create_commitment_router, create_pool_router
                app.include_router(create_commitment_router(db_pool))
                app.include_router(create_pool_router(db_pool))
                logger.info("Commitment routers mounted (/commitments/, /pools/)")
            except Exception as e:
                logger.warning(f"Commitment routers not mounted: {e}")

            # Claims engine router (always on, no capability gate)
            try:
                from api.routers.claims_router import create_router as create_claims_router
                app.include_router(create_claims_router(db_pool))
                logger.info("Claims router mounted (/claims/)")
            except Exception as e:
                logger.warning(f"Claims router not mounted: {e}")

            if _caps.github_sensor:
                try:
                    from api.routers.github_router import create_router as create_github_router
                    app.include_router(create_github_router(db_pool, _caps))
                    logger.info("GitHub router mounted")
                except Exception as e:
                    logger.warning(f"GitHub router not mounted: {e}")

            if _caps.mediawiki_sensor:
                try:
                    from api.mediawiki_sensor import MediaWikiSensor
                    mw_sensor = MediaWikiSensor(pool=db_pool, event_queue=getattr(app.state, 'event_queue', None))
                    await mw_sensor.start()
                    app.state.mediawiki_sensor = mw_sensor
                except Exception as e:
                    logger.warning(f"MediaWiki sensor not started: {e}")
                try:
                    from api.routers.mediawiki_router import create_router as create_mw_router
                    app.include_router(create_mw_router(db_pool, getattr(app.state, 'mediawiki_sensor', None)))
                    logger.info("MediaWiki router mounted")
                except Exception as e:
                    logger.warning(f"MediaWiki router not mounted: {e}")

            if _caps.coordinator_endpoints:
                try:
                    from api.routers.network_router import create_router as create_network_router
                    app.include_router(create_network_router(db_pool, _caps))
                    logger.info("Network router mounted")
                except Exception as e:
                    logger.warning(f"Network router not mounted: {e}")

        # Initialize KOI-net federation (if enabled)
        if KOI_NET_ENABLED:
            try:
                from api.koi_net_router import setup_koi_net
                await setup_koi_net(db_pool)
                logger.info("KOI-net federation initialized")
            except Exception as e:
                logger.warning(f"KOI-net federation failed to initialize: {e}")

        # Initialize TerminusDB adapter (if enabled)
        if TERMINUSDB_ENABLED:
            global terminusdb_adapter
            try:
                from api.terminusdb_adapter import TerminusDBAdapter
                terminusdb_adapter = TerminusDBAdapter(
                    url=os.getenv('TERMINUSDB_URL', 'http://127.0.0.1:6363/'),
                    db_name=os.getenv('TERMINUSDB_DB', 'koi_knowledge_graph'),
                    team=os.getenv('TERMINUSDB_TEAM', 'admin'),
                    key=os.getenv('TERMINUSDB_KEY', 'root'),
                )
                health = terminusdb_adapter.health()
                if health.get("terminusdb_reachable"):
                    logger.info(f"TerminusDB connected (schema_hash={health['schema_hash'][:12]}...)")
                else:
                    logger.warning(f"TerminusDB not reachable: {health.get('error', 'unknown')}")
                    logger.info("Outbox will accumulate; worker will drain on recovery")
            except Exception as e:
                logger.warning(f"TerminusDB initialization failed (non-fatal): {e}")
                terminusdb_adapter = None

    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise


@app.on_event("shutdown")
async def shutdown():
    """Close database connection pool"""
    global db_pool
    if db_pool:
        await db_pool.close()


async def ensure_schema(conn: asyncpg.Connection, embedding_dim: int = 1536):
    """Ensure the entity_registry table exists"""
    await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS entity_registry (
            id SERIAL PRIMARY KEY,
            fuseki_uri TEXT UNIQUE NOT NULL,
            entity_text TEXT NOT NULL,
            entity_type TEXT,
            normalized_text TEXT NOT NULL,
            ledger_id TEXT,
            metadata_iri TEXT,
            admin_address TEXT,
            aliases TEXT[],
            jurisdiction TEXT,
            class_id TEXT,
            source TEXT DEFAULT 'personal-vault',
            first_seen_rid TEXT,
            metadata JSONB,
            embedding vector({embedding_dim}),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Create index on normalized_text for fast lookups
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_registry_normalized
        ON entity_registry(normalized_text)
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_registry_type
        ON entity_registry(entity_type)
    """)

    # Create HNSW index for vector similarity search (matches migration 020)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_vector
        ON entity_registry USING hnsw (embedding vector_cosine_ops)
    """)

    # Create document_entity_links table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS document_entity_links (
            id SERIAL PRIMARY KEY,
            document_rid TEXT NOT NULL,
            entity_uri TEXT NOT NULL,
            mention_count INT DEFAULT 1,
            context TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(document_rid, entity_uri)
        )
    """)

    # Create entity_rid_mappings table for vault entity registration
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS entity_rid_mappings (
            id SERIAL PRIMARY KEY,
            vault_rid TEXT UNIQUE NOT NULL,
            vault_path TEXT NOT NULL,
            canonical_uri TEXT NOT NULL,
            entity_type TEXT,
            name TEXT,
            content_hash TEXT,
            sync_status TEXT DEFAULT 'linked',
            last_synced TIMESTAMP DEFAULT NOW(),
            created_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT valid_sync_status CHECK (
                sync_status IN ('linked', 'local_only', 'pending_sync', 'conflict')
            )
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rid_mappings_canonical
        ON entity_rid_mappings(canonical_uri)
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rid_mappings_vault_path
        ON entity_rid_mappings(vault_path)
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rid_mappings_sync_status
        ON entity_rid_mappings(sync_status)
    """)

    # Add vault_rid column to entity_registry if not exists
    try:
        await conn.execute("""
            ALTER TABLE entity_registry
            ADD COLUMN IF NOT EXISTS vault_rid TEXT
        """)
    except Exception:
        pass  # Column may already exist

    # ==========================================================================
    # Entity Relationships Tables (for relationship-aware entity resolution)
    # ==========================================================================

    # Enable pg_trgm extension for fuzzy matching in pending resolution
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Predicate allow-list (must be created first - FK target)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS allowed_predicates (
            predicate TEXT PRIMARY KEY,
            description TEXT,
            subject_types TEXT[],
            object_types TEXT[]
        )
    """)

    # Seed with canonical predicates (idempotent)
    await conn.execute("""
        INSERT INTO allowed_predicates (predicate, description, subject_types, object_types) VALUES
            ('affiliated_with', 'Person belongs to organization', ARRAY['Person'], ARRAY['Organization']),
            ('founded', 'Person founded org/project', ARRAY['Person'], ARRAY['Organization', 'Project']),
            ('has_founder', 'Org/project was founded by', ARRAY['Organization', 'Project'], ARRAY['Person']),
            ('knows', 'Person knows person (symmetric)', ARRAY['Person'], ARRAY['Person']),
            ('collaborates_with', 'Person collaborates with person (symmetric)', ARRAY['Person'], ARRAY['Person']),
            ('involves_person', 'Project involves person', ARRAY['Project', 'Meeting'], ARRAY['Person']),
            ('involves_organization', 'Project involves organization', ARRAY['Project'], ARRAY['Organization']),
            ('has_project', 'Organization has project', ARRAY['Organization'], ARRAY['Project']),
            ('attended', 'Person attended meeting', ARRAY['Person'], ARRAY['Meeting']),
            ('located_in', 'Entity is located in place', NULL, ARRAY['Location']),
            ('assigned_to', 'Task assigned to person', ARRAY['Task'], ARRAY['Person']),
            ('belongs_to_project', 'Task belongs to project', ARRAY['Task'], ARRAY['Project']),
            ('sourced_from', 'Task sourced from document', ARRAY['Task'], ARRAY['Meeting'])
        ON CONFLICT (predicate) DO NOTHING
    """)

    # Entity relationships table (resolved relationships)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS entity_relationships (
            id SERIAL PRIMARY KEY,
            subject_uri TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_uri TEXT NOT NULL,
            confidence FLOAT DEFAULT 1.0,
            source TEXT DEFAULT 'vault',
            source_rid TEXT,
            source_field TEXT,
            raw_value TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(subject_uri, predicate, object_uri),
            CHECK (subject_uri != object_uri),
            CHECK (predicate ~ '^[a-z][a-z0-9_]*$')
        )
    """)

    # Add FK constraints if they don't exist (ignore errors if already exist)
    try:
        await conn.execute("""
            ALTER TABLE entity_relationships
            ADD CONSTRAINT fk_rel_predicate FOREIGN KEY (predicate)
                REFERENCES allowed_predicates(predicate)
        """)
    except Exception:
        pass

    try:
        await conn.execute("""
            ALTER TABLE entity_relationships
            ADD CONSTRAINT fk_rel_subject FOREIGN KEY (subject_uri)
                REFERENCES entity_registry(fuseki_uri) ON DELETE CASCADE
        """)
    except Exception:
        pass

    try:
        await conn.execute("""
            ALTER TABLE entity_relationships
            ADD CONSTRAINT fk_rel_object FOREIGN KEY (object_uri)
                REFERENCES entity_registry(fuseki_uri) ON DELETE CASCADE
        """)
    except Exception:
        pass

    # Pending relationships table (unresolved targets)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_relationships (
            id SERIAL PRIMARY KEY,
            subject_uri TEXT,
            object_uri TEXT,
            predicate TEXT NOT NULL,
            raw_unknown_label TEXT NOT NULL,
            unknown_side TEXT NOT NULL CHECK (unknown_side IN ('subject', 'object')),
            target_type_hint TEXT,
            source TEXT DEFAULT 'vault',
            source_rid TEXT,
            source_field TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            CHECK ((subject_uri IS NOT NULL AND object_uri IS NULL) OR
                   (subject_uri IS NULL AND object_uri IS NOT NULL)),
            CHECK (
                (unknown_side = 'subject' AND subject_uri IS NULL AND object_uri IS NOT NULL) OR
                (unknown_side = 'object' AND object_uri IS NULL AND subject_uri IS NOT NULL)
            )
        )
    """)

    # Add unique index for pending edges (expression index)
    try:
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_unique_edge
            ON pending_relationships (
                COALESCE(subject_uri, ''),
                COALESCE(object_uri, ''),
                predicate,
                raw_unknown_label,
                unknown_side
            )
        """)
    except Exception:
        pass

    # Add FK constraints for pending
    try:
        await conn.execute("""
            ALTER TABLE pending_relationships
            ADD CONSTRAINT fk_pending_predicate FOREIGN KEY (predicate)
                REFERENCES allowed_predicates(predicate)
        """)
    except Exception:
        pass

    try:
        await conn.execute("""
            ALTER TABLE pending_relationships
            ADD CONSTRAINT fk_pending_subject FOREIGN KEY (subject_uri)
                REFERENCES entity_registry(fuseki_uri) ON DELETE CASCADE
        """)
    except Exception:
        pass

    try:
        await conn.execute("""
            ALTER TABLE pending_relationships
            ADD CONSTRAINT fk_pending_object FOREIGN KEY (object_uri)
                REFERENCES entity_registry(fuseki_uri) ON DELETE CASCADE
        """)
    except Exception:
        pass

    # Indexes for entity_relationships
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_subject ON entity_relationships(subject_uri)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_object ON entity_relationships(object_uri)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_subject_predicate ON entity_relationships(subject_uri, predicate)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_object_predicate ON entity_relationships(object_uri, predicate)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_source_rid ON entity_relationships(source_rid)")

    # Indexes for pending_relationships
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_unknown_label ON pending_relationships(raw_unknown_label)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_source_rid ON pending_relationships(source_rid)")

    # GIN trigram index for fuzzy matching
    try:
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_unknown_label_trgm
            ON pending_relationships USING GIN (raw_unknown_label gin_trgm_ops)
        """)
    except Exception:
        pass  # May fail if pg_trgm extension not available

    logger.info("Schema verified/created (including relationship tables)")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

        # Get loaded entity types from schema
        schemas = get_entity_schemas()
        entity_types = list(schemas.keys())

        return {
            "status": "healthy",
            "mode": KOI_MODE,
            "database": "connected",
            "embedding_available": embedding_provider is not None,
            "embedding_model": embedding_provider.model_name if embedding_provider else None,
            "embedding_dimension": embedding_provider.dimension if embedding_provider else None,
            "semantic_matching": embedding_provider is not None and ENABLE_SEMANTIC_MATCHING,
            "entity_types": entity_types,
            "schema_version": get_schema_version(),
            "resolution_tiers": {
                "tier1_exact": True,
                "tier1x_fuzzy": True,
                "tier15_contextual": True,
                "tier2_semantic": embedding_provider is not None and ENABLE_SEMANTIC_MATCHING,
                "tier3_create": True
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )


@app.get("/graph-version")
async def graph_version_endpoint():
    """Return a deterministic hash of graph state for eval snapshot pinning.

    Hash = SHA-256(entity_count:rel_count:max_entity_updated:max_rel_created)[:16]
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM entity_registry WHERE NOT node_private) AS entity_count,
                (SELECT COUNT(*) FROM entity_relationships) AS rel_count,
                (SELECT COALESCE(MAX(updated_at), '1970-01-01'::timestamptz) FROM entity_registry) AS max_entity_updated,
                (SELECT COALESCE(GREATEST(MAX(created_at), MAX(updated_at)), '1970-01-01'::timestamptz) FROM entity_relationships) AS max_rel_changed
        """)
        import hashlib as _hl
        state = f"{row['entity_count']}:{row['rel_count']}:{row['max_entity_updated']}:{row['max_rel_changed']}"
        version_hash = _hl.sha256(state.encode()).hexdigest()[:16]
        return {
            "graph_version": version_hash,
            "entity_count": row['entity_count'],
            "relationship_count": row['rel_count'],
        }


@app.get("/entity-types")
async def get_entity_types_endpoint():
    """
    Return entity type configs for MCP and external tools.

    This is the source of truth for entity type configuration.
    MCP and other clients should call this endpoint instead of
    maintaining their own hardcoded type mappings.

    Returns:
        version: Schema version hash for cache invalidation
        types: List of entity type configurations
    """
    schemas = get_entity_schemas()
    return {
        "version": get_schema_version(),
        "types": [
            {
                "type_key": s.type_key,
                "label": s.label,
                "folder": s.folder,
                "phonetic_matching": s.phonetic_matching,
                "min_context_people": s.min_context_people,
                "similarity_threshold": s.similarity_threshold,
                "semantic_threshold": s.semantic_threshold,
                "require_token_overlap": s.require_token_overlap,
            }
            for s in schemas.values()
        ]
    }


@app.post("/reload-schemas")
async def reload_schemas_endpoint(vault_path: Optional[str] = None):
    """
    Hot reload entity schemas from vault without restart.

    This endpoint reloads schemas from the vault Ontology/ folder.
    Use after adding or modifying schema files.

    Args:
        vault_path: Optional override for vault path (default: ~/Documents/Notes)

    Returns:
        version: New schema version hash
        types: Updated list of entity type keys
        phonetic_enabled: Types with phonetic matching enabled
    """
    schemas = reload_entity_schemas(vault_path)
    return {
        "success": True,
        "version": get_schema_version(),
        "types": list(schemas.keys()),
        "phonetic_enabled": get_phonetic_enabled_types(),
        "count": len(schemas)
    }


@app.post("/ingest", response_model=IngestResponse)
async def ingest_extraction(request: IngestRequest):
    """
    Ingest pre-extracted entities from Claude Code.

    This endpoint:
    1. Deduplicates entities against the personal KB
    2. Assigns canonical URIs to new entities
    3. Stores entities and document links
    4. Returns resolved entities for vault linking
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    canonical_entities: List[CanonicalEntity] = []
    new_count = 0
    resolved_count = 0
    failed_entities: List[dict] = []
    entity_uri_map: Dict[str, str] = {}  # normalized_name → canonical URI

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for entity in request.entities:
                try:
                    logger.info(f"Processing entity: {entity.name} ({entity.type})")

                    # Build context for this entity, merging global + per-entity (with deduplication)
                    global_people = request.context.associated_people if request.context else []
                    entity_people = entity.associated_people or []
                    global_orgs = request.context.organizations if request.context else []
                    entity_orgs = entity.associated_organizations or []

                    context_for_entity = ResolutionContext(
                        associated_people=list(set((global_people or []) + entity_people)),
                        organizations=list(set((global_orgs or []) + entity_orgs)),
                        project=request.context.project if request.context else None,
                        topics=request.context.topics if request.context else []
                    ) if (global_people or entity_people or global_orgs or entity_orgs or
                          (request.context and request.context.project)) else request.context

                    canonical, is_new = await resolve_entity(conn, entity, context_for_entity)
                    logger.info(f"Resolved: {canonical.name} -> {canonical.uri} (new={is_new})")
                    canonical_entities.append(canonical)
                    entity_uri_map[normalize_entity_text(entity.name)] = canonical.uri

                    if is_new:
                        new_count += 1
                        await store_new_entity(conn, entity, canonical, request.document_rid, source=request.source)
                        logger.info(f"Stored new entity: {canonical.uri}")
                    else:
                        resolved_count += 1
                        logger.info(f"Resolved to existing: {canonical.uri}")

                    # Link entity to document
                    await conn.execute("""
                        INSERT INTO document_entity_links (document_rid, entity_uri, context)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (document_rid, entity_uri)
                        DO UPDATE SET mention_count = document_entity_links.mention_count + 1
                    """, request.document_rid, canonical.uri, entity.context)
                    logger.info(f"Linked entity to document")

                except Exception as e:
                    import traceback
                    logger.error(f"Error processing entity {entity.name}: {e}")
                    logger.error(traceback.format_exc())
                    failed_entities.append({"name": entity.name, "error": str(e)})
                    # Continue with other entities

            # --- Process relationships (still inside conn.transaction()) ---
            rel_count = 0
            if request.relationships:
                for rel in request.relationships:
                    subj_key = normalize_entity_text(rel.subject)
                    obj_key = normalize_entity_text(rel.object)
                    subj_uri = entity_uri_map.get(subj_key)
                    obj_uri = entity_uri_map.get(obj_key)

                    # Fallback: look up pre-existing entities by exact normalized_text
                    if not subj_uri:
                        rows = await conn.fetch(
                            "SELECT fuseki_uri FROM entity_registry WHERE normalized_text = $1 LIMIT 2",
                            subj_key)
                        if len(rows) == 1:
                            subj_uri = rows[0]["fuseki_uri"]
                        elif len(rows) > 1:
                            logger.warning(
                                f"Ambiguous subject '{rel.subject}' in relationship "
                                f"'{rel.subject} → {rel.predicate} → {rel.object}': "
                                f"{len(rows)}+ matches, skipping")
                    if not obj_uri:
                        rows = await conn.fetch(
                            "SELECT fuseki_uri FROM entity_registry WHERE normalized_text = $1 LIMIT 2",
                            obj_key)
                        if len(rows) == 1:
                            obj_uri = rows[0]["fuseki_uri"]
                        elif len(rows) > 1:
                            logger.warning(
                                f"Ambiguous object '{rel.object}' in relationship "
                                f"'{rel.subject} → {rel.predicate} → {rel.object}': "
                                f"{len(rows)}+ matches, skipping")

                    if not subj_uri or not obj_uri:
                        logger.warning(
                            f"Skipping relationship {rel.subject} → {rel.predicate} → {rel.object}: "
                            f"subject={'found' if subj_uri else 'NOT found'}, "
                            f"object={'found' if obj_uri else 'NOT found'}"
                        )
                        continue

                    if subj_uri == obj_uri:
                        logger.warning(
                            f"Skipping self-referential relationship: {rel.subject} → {rel.predicate} → {rel.object}")
                        continue

                    try:
                        await conn.execute("""
                            INSERT INTO entity_relationships
                                (subject_uri, predicate, object_uri, source, source_rid)
                            VALUES ($1, $2, $3, $4, $5)
                            ON CONFLICT (subject_uri, predicate, object_uri) DO NOTHING
                        """, subj_uri, rel.predicate, obj_uri,
                            request.source, request.document_rid)
                        rel_count += 1
                    except asyncpg.exceptions.ForeignKeyViolationError as e:
                        logger.warning(f"Skipping relationship (FK violation): {e}")
                    except asyncpg.exceptions.CheckViolationError as e:
                        logger.warning(f"Skipping relationship (check violation): {e}")

    # Generate receipt RID
    receipt_rid = f"orn:personal-koi.receipt:{uuid.uuid4().hex[:16]}"

    success = len(failed_entities) == 0
    stats = IngestStats(
        entities_processed=len(request.entities),
        new_entities=new_count,
        resolved_entities=resolved_count,
        relationships_processed=rel_count,
        failed_entities=len(failed_entities),
        errors=failed_entities if failed_entities else None
    )
    if failed_entities:
        logger.warning(f"Ingest completed with {len(failed_entities)} failures: "
                      f"{[f['name'] for f in failed_entities]}")

    return IngestResponse(
        success=success,
        canonical_entities=canonical_entities,
        receipt_rid=receipt_rid,
        stats=stats
    )


@app.get("/entities")
async def list_entities(
    entity_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """List entities in the knowledge base"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        if entity_type:
            entities = await conn.fetch("""
                SELECT fuseki_uri, entity_text, entity_type, source, created_at
                FROM entity_registry
                WHERE entity_type = $1 AND NOT node_private
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
            """, entity_type, limit, offset)
        else:
            entities = await conn.fetch("""
                SELECT fuseki_uri, entity_text, entity_type, source, created_at
                FROM entity_registry
                WHERE NOT node_private
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
            """, limit, offset)

        return {
            "entities": [dict(e) for e in entities],
            "count": len(entities),
            "limit": limit,
            "offset": offset
        }


@app.api_route("/entity-search", methods=["GET", "POST"])
async def entity_search(request: Request, query: str = None, limit: int = 20, entity_type: Optional[str] = None):
    """Search entities by name (fuzzy text match + optional semantic).
    GET: query params (?query=...&limit=20&entity_type=Project)
    POST: JSON body ({"query": "...", "limit": 20, "entity_type": "Project"})
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    # For POST, merge body params over query params
    if request.method == "POST":
        try:
            body = await request.json()
            query = body.get("query", query)
            limit = body.get("limit", limit)
            entity_type = body.get("entity_type", entity_type)
        except Exception:
            pass

    if not query:
        return JSONResponse({"results": [], "count": 0})

    normalized_query = normalize_entity_text(query)
    async with db_pool.acquire() as conn:
        # Text search: ILIKE on normalized_text
        if entity_type:
            rows = await conn.fetch("""
                SELECT fuseki_uri, entity_text, entity_type, source, created_at, aliases,
                       CASE WHEN normalized_text = $1 THEN 1.0
                            WHEN normalized_text ILIKE $2 THEN 0.9
                            ELSE 0.7 END AS similarity
                FROM entity_registry
                WHERE normalized_text ILIKE $2 AND entity_type = $3 AND NOT node_private
                ORDER BY
                    CASE WHEN normalized_text = $1 THEN 0 ELSE 1 END,
                    created_at DESC
                LIMIT $4
            """, normalized_query, f"%{normalized_query}%", entity_type, limit)
        else:
            rows = await conn.fetch("""
                SELECT fuseki_uri, entity_text, entity_type, source, created_at, aliases,
                       CASE WHEN normalized_text = $1 THEN 1.0
                            WHEN normalized_text ILIKE $2 THEN 0.9
                            ELSE 0.7 END AS similarity
                FROM entity_registry
                WHERE normalized_text ILIKE $2 AND NOT node_private
                ORDER BY
                    CASE WHEN normalized_text = $1 THEN 0 ELSE 1 END,
                    created_at DESC
                LIMIT $3
            """, normalized_query, f"%{normalized_query}%", limit)

        # Get relationship counts for matched entities
        uris = [r["fuseki_uri"] for r in rows]
        rel_counts: Dict[str, int] = {}
        if uris:
            rel_rows = await conn.fetch("""
                SELECT uri, COUNT(*) as cnt FROM (
                    SELECT subject_uri AS uri FROM entity_relationships WHERE subject_uri = ANY($1)
                    UNION ALL
                    SELECT object_uri AS uri FROM entity_relationships WHERE object_uri = ANY($1)
                ) sub GROUP BY uri
            """, uris)
            rel_counts = {r["uri"]: r["cnt"] for r in rel_rows}

        results = []
        for row in rows:
            uri = row["fuseki_uri"]
            entity_type = row["entity_type"]
            entity_name = row["entity_text"]
            results.append({
                "fuseki_uri": uri,
                "name": entity_name,
                "entity_type": entity_type,
                "similarity": float(row["similarity"]),
                "aliases": list(row["aliases"] or []),
                "relationship_count": rel_counts.get(uri, 0),
                "quartz_url": quartz_url(entity_type, entity_name),
            })

    return {"results": results, "count": len(results)}


@app.get("/entity/resolve")
async def resolve_entity_get(
    label: str,
    type_hint: Optional[str] = None,
    limit: int = 5
):
    """
    Resolve an entity label to canonical entity (GET - backward compatible).

    Query Parameters:
        label: Entity name to resolve
        type_hint: Optional entity type filter
        limit: Maximum candidates (default 5)

    Returns candidates with URIs and confidence scores.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    entity = ExtractedEntity(name=label, type=type_hint or "")
    async with db_pool.acquire() as conn:
        canonical, is_new = await resolve_entity(conn, entity, context=None)

        if canonical is None:
            return {"candidates": [], "is_new": False}

        # Hide node_private entities from public resolution
        if not is_new:
            is_private = await conn.fetchval(
                "SELECT node_private FROM entity_registry WHERE fuseki_uri = $1",
                canonical.uri
            )
            if is_private:
                return {"candidates": [], "is_new": False}

    return {
        "candidates": [{
            "name": canonical.name,
            "uri": canonical.uri,
            "type": canonical.type,
            "confidence": canonical.confidence,
            "merged_with": canonical.merged_with
        }],
        "is_new": is_new
    }


@app.post("/entity/resolve")
async def resolve_entity_post(request: ResolveRequest):
    """
    Resolve an entity label to canonical entity with optional context (POST).

    Request Body:
        label: Entity name to resolve
        type_hint: Optional entity type filter
        limit: Maximum candidates (default 5)
        context: Optional disambiguation context
            - associated_people: List of people co-occurring with this entity

    The context parameter enables Tier 1.5 contextual matching, which uses
    co-occurrence in documents to disambiguate entities. For example:
    - "Biocene Labs" with associated_people=["Shawn Anderson", "Darren Zal"]
      may resolve to "Symbiocene Labs" if those people appear together in
      documents mentioning Symbiocene Labs.

    Returns candidates with URIs and confidence scores (same format as GET).
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    entity = ExtractedEntity(name=request.label, type=request.type_hint or "")
    async with db_pool.acquire() as conn:
        canonical, is_new = await resolve_entity(conn, entity, request.context)

        if canonical is None:
            return {"candidates": [], "is_new": False}

        # Hide node_private entities from public resolution
        if not is_new:
            is_private = await conn.fetchval(
                "SELECT node_private FROM entity_registry WHERE fuseki_uri = $1",
                canonical.uri
            )
            if is_private:
                return {"candidates": [], "is_new": False}

    return {
        "candidates": [{
            "name": canonical.name,
            "uri": canonical.uri,
            "type": canonical.type,
            "confidence": canonical.confidence,
            "merged_with": canonical.merged_with
        }],
        "is_new": is_new
    }


# =============================================================================
# Entity MentionedIn Endpoints (for bidirectional linking)
# IMPORTANT: These must be defined BEFORE the generic /entity/{uri:path} route
# =============================================================================

class EvidenceItem(BaseModel):
    subject_uri: str
    subject_name: str
    predicate: str
    object_uri: str
    object_name: str
    confidence: Optional[float] = None
    source: Optional[str] = None
    source_section: Optional[str] = None
    wiki_url: Optional[str] = None


class EvidenceResponse(BaseModel):
    entity_uri: str
    entity_name: str
    evidence: List[EvidenceItem]
    total: int


class MentionedInDocument(BaseModel):
    """A document that mentions an entity"""
    vault_path: str  # NO .md extension
    document_rid: str
    mention_count: int
    doc_date: Optional[str] = None
    first_seen: str


class MentionedInResponse(BaseModel):
    """Response from mentioned-in endpoint"""
    entity_uri: str
    total_count: int
    truncated: bool
    documents: List[MentionedInDocument]


class BatchMentionedInRequest(BaseModel):
    """Request for batch mentioned-in query"""
    uris: List[str]
    limit_per_entity: int = 500


class BatchMentionedInResponse(BaseModel):
    """Response from batch mentioned-in query"""
    results: Dict[str, MentionedInResponse]
    total_entities: int


def extract_date_from_vault_path(vault_path: str) -> Optional[str]:
    """
    Extract date from vault path if present.
    Looks for YYYY-MM-DD pattern at start of filename.
    """
    import re
    # Get filename from path
    filename = vault_path.split('/')[-1] if '/' in vault_path else vault_path
    # Remove .md extension if present
    if filename.endswith('.md'):
        filename = filename[:-3]
    # Look for date pattern at start
    match = re.match(r'^(\d{4}-\d{2}-\d{2})', filename)
    return match.group(1) if match else None


@app.get("/entity/{entity_uri:path}/mentioned-in", response_model=MentionedInResponse)
async def get_entity_mentioned_in(
    entity_uri: str,
    limit: int = 500
):
    """
    Get documents that mention an entity.

    This endpoint queries document_entity_links to find all documents
    that mention the given entity. Used for populating mentionedIn
    frontmatter in entity notes.

    Args:
        entity_uri: Canonical entity URI (orn:personal-koi.entity:...)
        limit: Maximum documents to return (default 500, high to avoid silent truncation)

    Returns:
        MentionedInResponse with sorted document list (alphabetical by vault_path)
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Reject queries for node_private entities
        is_private = await conn.fetchval(
            "SELECT node_private FROM entity_registry WHERE fuseki_uri = $1",
            entity_uri
        )
        if is_private:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Query document_entity_links for documents mentioning this entity
        rows = await conn.fetch("""
            SELECT del.document_rid, del.mention_count, del.created_at
            FROM document_entity_links del
            WHERE del.entity_uri = $1
            ORDER BY del.document_rid ASC
            LIMIT $2
        """, entity_uri, limit + 1)  # Fetch limit+1 to detect truncation

        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]

        documents = []
        for row in rows:
            # Convert document_rid to vault_path
            # document_rid format: "orn:obsidian.entity:Notes/..." or "vault:notes/..."
            doc_rid = row['document_rid']

            # Extract vault path from RID
            # Handles various formats: orn:obsidian.entity:Notes/, vault:notes/, vault:
            if doc_rid.startswith('orn:obsidian.entity:Notes/'):
                vault_path = doc_rid.replace('orn:obsidian.entity:Notes/', '')
            elif doc_rid.startswith('vault:notes/'):
                vault_path = doc_rid.replace('vault:notes/', '')
            elif doc_rid.startswith('vault:'):
                vault_path = doc_rid.replace('vault:', '')
            else:
                vault_path = doc_rid

            # Remove .md extension for Obsidian wikilink format
            if vault_path.endswith('.md'):
                vault_path = vault_path[:-3]

            # Extract date from filename if present
            doc_date = extract_date_from_vault_path(vault_path)

            documents.append(MentionedInDocument(
                vault_path=vault_path,
                document_rid=doc_rid,
                mention_count=row['mention_count'] or 1,
                doc_date=doc_date,
                first_seen=row['created_at'].isoformat() if row['created_at'] else None
            ))

        return MentionedInResponse(
            entity_uri=entity_uri,
            total_count=len(documents),
            truncated=truncated,
            documents=documents
        )


@app.post("/entities/mentioned-in", response_model=BatchMentionedInResponse)
async def get_entities_mentioned_in_batch(request: BatchMentionedInRequest):
    """
    Batch query for documents mentioning multiple entities.

    This endpoint allows efficient querying of multiple entities at once,
    avoiding N+1 API calls when propagating mentionedIn to many entity notes.

    Args:
        uris: List of entity URIs to query
        limit_per_entity: Maximum documents per entity (default 500)

    Returns:
        BatchMentionedInResponse with results keyed by entity URI
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    if not request.uris:
        return BatchMentionedInResponse(results={}, total_entities=0)

    results = {}

    async with db_pool.acquire() as conn:
        # Filter out node_private entities from the batch
        private_uris = set()
        if request.uris:
            private_rows = await conn.fetch(
                "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = ANY($1) AND node_private",
                request.uris
            )
            private_uris = {r['fuseki_uri'] for r in private_rows}

        for entity_uri in request.uris:
            if entity_uri in private_uris:
                continue
            # Query for each entity
            rows = await conn.fetch("""
                SELECT del.document_rid, del.mention_count, del.created_at
                FROM document_entity_links del
                WHERE del.entity_uri = $1
                ORDER BY del.document_rid ASC
                LIMIT $2
            """, entity_uri, request.limit_per_entity + 1)

            truncated = len(rows) > request.limit_per_entity
            if truncated:
                rows = rows[:request.limit_per_entity]

            documents = []
            for row in rows:
                doc_rid = row['document_rid']

                # Extract vault path from RID
                # Handles various formats: orn:obsidian.entity:Notes/, vault:notes/, vault:
                if doc_rid.startswith('orn:obsidian.entity:Notes/'):
                    vault_path = doc_rid.replace('orn:obsidian.entity:Notes/', '')
                elif doc_rid.startswith('vault:notes/'):
                    vault_path = doc_rid.replace('vault:notes/', '')
                elif doc_rid.startswith('vault:'):
                    vault_path = doc_rid.replace('vault:', '')
                else:
                    vault_path = doc_rid

                if vault_path.endswith('.md'):
                    vault_path = vault_path[:-3]

                doc_date = extract_date_from_vault_path(vault_path)

                documents.append(MentionedInDocument(
                    vault_path=vault_path,
                    document_rid=doc_rid,
                    mention_count=row['mention_count'] or 1,
                    doc_date=doc_date,
                    first_seen=row['created_at'].isoformat() if row['created_at'] else None
                ))

            results[entity_uri] = MentionedInResponse(
                entity_uri=entity_uri,
                total_count=len(documents),
                truncated=truncated,
                documents=documents
            )

    return BatchMentionedInResponse(
        results=results,
        total_entities=len(results)
    )


# =============================================================================
# Evidence Provenance Endpoint
# =============================================================================

@app.get("/entity/{entity_uri:path}/evidence", response_model=EvidenceResponse)
async def get_entity_evidence(entity_uri: str):
    """Get evidence provenance for an entity's relationships.

    Returns all relationships involving this entity with source provenance,
    including MediaWiki page links and wiki URLs where available.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Look up entity name
        entity = await conn.fetchrow(
            "SELECT entity_text FROM entity_registry WHERE fuseki_uri = $1 AND NOT node_private",
            entity_uri
        )
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        entity_name = entity["entity_text"]

        # Get wiki base URL (if any wiki is registered)
        wiki_base_url = None
        try:
            wiki_base_url = await conn.fetchval(
                "SELECT base_url FROM mediawiki_wikis ORDER BY id LIMIT 1"
            )
        except asyncpg.UndefinedTableError:
            pass  # migration 063 not applied

        # Query relationships with provenance
        try:
            rows = await conn.fetch("""
                SELECT
                    er.subject_uri,
                    subj.entity_text AS subject_name,
                    er.predicate,
                    er.object_uri,
                    obj.entity_text AS object_name,
                    er.confidence,
                    er.source,
                    mpl.source_section,
                    mps.title AS wiki_page_title
                FROM entity_relationships er
                JOIN entity_registry subj ON subj.fuseki_uri = er.subject_uri
                JOIN entity_registry obj ON obj.fuseki_uri = er.object_uri
                LEFT JOIN mediawiki_page_state mps ON mps.source_rid = er.source_rid
                LEFT JOIN mediawiki_page_links mpl ON mpl.source_page_id = mps.id
                    AND mpl.target_title = obj.entity_text
                    AND mpl.predicate = er.predicate
                WHERE (er.subject_uri = $1 OR er.object_uri = $1)
                ORDER BY er.confidence DESC NULLS LAST
                LIMIT 100
            """, entity_uri)
        except asyncpg.UndefinedTableError:
            # mediawiki tables don't exist — fall back without provenance
            rows = await conn.fetch("""
                SELECT
                    er.subject_uri,
                    subj.entity_text AS subject_name,
                    er.predicate,
                    er.object_uri,
                    obj.entity_text AS object_name,
                    er.confidence,
                    er.source,
                    NULL::text AS source_section,
                    NULL::text AS wiki_page_title
                FROM entity_relationships er
                JOIN entity_registry subj ON subj.fuseki_uri = er.subject_uri
                JOIN entity_registry obj ON obj.fuseki_uri = er.object_uri
                WHERE (er.subject_uri = $1 OR er.object_uri = $1)
                ORDER BY er.confidence DESC NULLS LAST
                LIMIT 100
            """, entity_uri)

        evidence = []
        for row in rows:
            wiki_url = None
            if wiki_base_url and row["wiki_page_title"]:
                page_path = row["wiki_page_title"].replace(" ", "_")
                wiki_url = f"{wiki_base_url.rstrip('/')}/wiki/{page_path}"
                if row["source_section"]:
                    wiki_url += f"#{row['source_section']}"

            evidence.append(EvidenceItem(
                subject_uri=row["subject_uri"],
                subject_name=row["subject_name"],
                predicate=row["predicate"],
                object_uri=row["object_uri"],
                object_name=row["object_name"],
                confidence=row["confidence"],
                source=row["source"],
                source_section=row["source_section"],
                wiki_url=wiki_url,
            ))

        return EvidenceResponse(
            entity_uri=entity_uri,
            entity_name=entity_name,
            evidence=evidence,
            total=len(evidence),
        )


# =============================================================================
# Entity CRUD Endpoints
# =============================================================================

@app.get("/entity/{entity_uri:path}")
async def get_entity(entity_uri: str):
    """Get a specific entity by URI"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        entity = await conn.fetchrow("""
            SELECT fuseki_uri, entity_text, entity_type, normalized_text,
                   source, first_seen_rid, metadata, created_at
            FROM entity_registry
            WHERE fuseki_uri = $1 AND NOT node_private
        """, entity_uri)

        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Get linked documents
        docs = await conn.fetch("""
            SELECT document_rid, mention_count, context, created_at
            FROM document_entity_links
            WHERE entity_uri = $1
            ORDER BY created_at DESC
        """, entity_uri)

        return {
            "entity": dict(entity),
            "documents": [dict(d) for d in docs]
        }


@app.get("/stats")
async def get_stats():
    """Get knowledge base statistics"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM entity_registry WHERE NOT node_private")

        by_type = await conn.fetch("""
            SELECT entity_type, COUNT(*) as count
            FROM entity_registry
            WHERE NOT node_private
            GROUP BY entity_type
            ORDER BY count DESC
        """)

        recent = await conn.fetch("""
            SELECT entity_text, entity_type, created_at
            FROM entity_registry
            WHERE NOT node_private
            ORDER BY created_at DESC
            LIMIT 10
        """)

        return {
            "total_entities": total,
            "by_type": {r['entity_type']: r['count'] for r in by_type},
            "recent_entities": [dict(r) for r in recent],
            "mode": KOI_MODE
        }


async def _recompute_node_private(conn, canonical_uri: str):
    """Set node_private=true only when ALL rid_mappings are node_private."""
    row = await conn.fetchrow("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE visibility_scope = 'node_private') AS private_count
        FROM entity_rid_mappings
        WHERE canonical_uri = $1
    """, canonical_uri)
    is_private = row['total'] > 0 and row['total'] == row['private_count']
    await conn.execute(
        "UPDATE entity_registry SET node_private = $1 WHERE fuseki_uri = $2",
        is_private, canonical_uri
    )


@app.post("/register-entity", response_model=RegisterEntityResponse)
async def register_vault_entity(request: RegisterEntityRequest):
    """
    Register a vault entity note with the backend.

    This endpoint:
    1. Checks if entity already exists (by name + type)
    2. Creates new or links to existing canonical entity
    3. Stores the vault RID → canonical URI mapping
    4. Returns canonical URI for frontmatter update
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Create an ExtractedEntity from the request for resolution
            entity = ExtractedEntity(
                name=request.name,
                type=request.entity_type,
                mentions=[request.name],
                confidence=1.0,
                context=None
            )

            # Resolve against existing entities
            canonical, is_new = await resolve_entity(conn, entity)

            # Check for URI collision with different vault file
            collision_warning = None
            if not is_new:
                existing_mapping = await conn.fetchrow("""
                    SELECT vault_path, name FROM entity_rid_mappings
                    WHERE canonical_uri = $1 AND vault_path != $2
                """, canonical.uri, request.vault_path)

                suppress_types = {'Meeting', 'Task'}
                suppress_paths = {'Tests/'}
                is_suppressed = (
                    request.entity_type in suppress_types
                    or any(request.vault_path.startswith(p) for p in suppress_paths)
                )
                if existing_mapping and not is_suppressed:
                    collision_warning = (
                        f"URI collision: '{request.name}' ({request.vault_path}) "
                        f"shares URI with '{existing_mapping['name']}' ({existing_mapping['vault_path']})"
                    )
                    logger.warning(collision_warning)

            if is_new:
                # Store new entity
                await store_new_entity(conn, entity, canonical, request.vault_rid)
                logger.info(f"Registered new entity: {canonical.uri}")
            else:
                logger.info(f"Linked to existing entity: {canonical.uri}")

            # Store or update RID mapping
            await conn.execute("""
                INSERT INTO entity_rid_mappings (
                    vault_rid, vault_path, canonical_uri, entity_type,
                    name, content_hash, sync_status, last_synced, visibility_scope
                ) VALUES ($1, $2, $3, $4, $5, $6, 'linked', NOW(), $7)
                ON CONFLICT (vault_rid) DO UPDATE SET
                    vault_path = EXCLUDED.vault_path,
                    canonical_uri = EXCLUDED.canonical_uri,
                    entity_type = EXCLUDED.entity_type,
                    name = EXCLUDED.name,
                    content_hash = EXCLUDED.content_hash,
                    sync_status = 'linked',
                    last_synced = NOW(),
                    visibility_scope = EXCLUDED.visibility_scope
            """,
                request.vault_rid,
                request.vault_path,
                canonical.uri,
                request.entity_type,
                request.name,
                request.content_hash,
                request.visibility_scope or "public"
            )

            # Update entity_registry with vault_rid if not set
            await conn.execute("""
                UPDATE entity_registry
                SET vault_rid = $1
                WHERE fuseki_uri = $2 AND vault_rid IS NULL
            """, request.vault_rid, canonical.uri)

            # Auto-assign koi_rid for federated publication scope
            final_koi_rid = None
            if request.publication_scope == "federated":
                existing_koi_rid = await conn.fetchval(
                    "SELECT koi_rid FROM entity_registry WHERE fuseki_uri = $1",
                    canonical.uri
                )
                if not existing_koi_rid:
                    import hashlib as _hashlib
                    type_slug = request.entity_type.lower()
                    h = _hashlib.sha256(canonical.uri.encode()).hexdigest()[:32]
                    koi_rid = f"orn:koi-net.{type_slug}:{h}"
                    await conn.execute(
                        "UPDATE entity_registry SET koi_rid = $1 WHERE fuseki_uri = $2",
                        koi_rid, canonical.uri
                    )
                    logger.info(f"Auto-assigned koi_rid for federated entity: {koi_rid}")
                final_koi_rid = await conn.fetchval(
                    "SELECT koi_rid FROM entity_registry WHERE fuseki_uri = $1",
                    canonical.uri
                )

            # Recompute node_private flag based on all mappings for this entity
            await _recompute_node_private(conn, canonical.uri)

            # Sync relationships from frontmatter if provided
            # Accept frontmatter OR properties (for older MCP clients)
            rel_stats = None
            frontmatter_data = request.frontmatter or request.properties
            if frontmatter_data:
                try:
                    rel_stats = await sync_vault_relationships(
                        conn,
                        request.vault_path,
                        canonical.uri,
                        frontmatter_data
                    )
                    logger.info(f"Synced relationships: {rel_stats}")

                    # Enqueue relationship changes to TerminusDB outbox
                    if rel_stats and TERMINUSDB_ENABLED:
                        await _enqueue_relationship_outbox(
                            conn, canonical.uri, request.vault_path)
                except Exception as e:
                    logger.warning(f"Failed to sync relationships: {e}")

                # Update aliases in entity_registry if provided in frontmatter
                raw_aliases = frontmatter_data.get('aliases', [])
                if raw_aliases:
                    if isinstance(raw_aliases, str):
                        raw_aliases = [raw_aliases]
                    normalized_aliases = [normalize_alias(a) for a in raw_aliases if a]

                    if normalized_aliases:
                        try:
                            # Merge with existing aliases using DISTINCT to prevent duplicates
                            await conn.execute("""
                                UPDATE entity_registry
                                SET aliases = (
                                    SELECT ARRAY(
                                        SELECT DISTINCT unnest(
                                            array_cat(COALESCE(aliases, '{}'), $1::TEXT[])
                                        )
                                    )
                                )
                                WHERE fuseki_uri = $2
                            """, normalized_aliases, canonical.uri)
                            logger.info(f"Updated aliases for {canonical.uri}: {normalized_aliases}")
                        except Exception as e:
                            logger.warning(f"Failed to update aliases: {e}")

            # Resolve any pending relationships that match this new entity
            pending_promoted = 0
            if is_new:
                try:
                    pending_promoted = await resolve_pending_relationships(
                        conn,
                        canonical.uri,
                        request.name,
                        request.entity_type
                    )
                    if pending_promoted > 0:
                        logger.info(f"Promoted {pending_promoted} pending relationship(s)")
                except Exception as e:
                    logger.warning(f"Failed to resolve pending relationships: {e}")

            return RegisterEntityResponse(
                success=True,
                canonical_uri=canonical.uri,
                is_new=is_new,
                vault_rid=request.vault_rid,
                merged_with=canonical.merged_with,
                collision_warning=collision_warning,
                koi_rid=final_koi_rid if request.publication_scope == "federated" else None
            )


@app.get("/vault-entities")
async def list_vault_entities(
    entity_type: Optional[str] = None,
    sync_status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """List all vault entities registered with the backend"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Build query with optional filters
        conditions = ["COALESCE(visibility_scope, 'public') != 'node_private'"]
        params = []
        param_idx = 1

        if entity_type:
            conditions.append(f"entity_type = ${param_idx}")
            params.append(entity_type)
            param_idx += 1

        if sync_status:
            conditions.append(f"sync_status = ${param_idx}")
            params.append(sync_status)
            param_idx += 1

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Add limit and offset
        params.extend([limit, offset])

        query = f"""
            SELECT vault_rid, vault_path, canonical_uri, entity_type,
                   name, sync_status, content_hash, last_synced
            FROM entity_rid_mappings
            {where_clause}
            ORDER BY last_synced DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """

        entities = await conn.fetch(query, *params)

        # Get total count (exclude limit/offset params)
        count_query = f"SELECT COUNT(*) FROM entity_rid_mappings {where_clause}"
        count_params = params[:-2]  # Strip limit/offset
        if count_params:
            total = await conn.fetchval(count_query, *count_params)
        else:
            total = await conn.fetchval(count_query)

        return {
            "entities": [
                {
                    "vault_rid": e['vault_rid'],
                    "vault_path": e['vault_path'],
                    "canonical_uri": e['canonical_uri'],
                    "entity_type": e['entity_type'],
                    "name": e['name'],
                    "sync_status": e['sync_status'],
                    "content_hash": e['content_hash'],
                    "last_synced": e['last_synced'].isoformat() if e['last_synced'] else None
                }
                for e in entities
            ],
            "count": total,
            "limit": limit,
            "offset": offset
        }


@app.get("/vault-entity/{vault_rid:path}")
async def get_vault_entity(vault_rid: str):
    """Get a specific vault entity by its RID"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        mapping = await conn.fetchrow("""
            SELECT vault_rid, vault_path, canonical_uri, entity_type,
                   name, sync_status, content_hash, last_synced, created_at
            FROM entity_rid_mappings
            WHERE vault_rid = $1
              AND COALESCE(visibility_scope, 'public') != 'node_private'
        """, vault_rid)

        if not mapping:
            raise HTTPException(status_code=404, detail="Vault entity not found")

        # Get the canonical entity details
        entity = await conn.fetchrow("""
            SELECT fuseki_uri, entity_text, entity_type, normalized_text,
                   source, first_seen_rid, metadata, created_at
            FROM entity_registry
            WHERE fuseki_uri = $1
        """, mapping['canonical_uri'])

        return {
            "mapping": {
                "vault_rid": mapping['vault_rid'],
                "vault_path": mapping['vault_path'],
                "canonical_uri": mapping['canonical_uri'],
                "entity_type": mapping['entity_type'],
                "name": mapping['name'],
                "sync_status": mapping['sync_status'],
                "content_hash": mapping['content_hash'],
                "last_synced": mapping['last_synced'].isoformat() if mapping['last_synced'] else None,
                "created_at": mapping['created_at'].isoformat() if mapping['created_at'] else None
            },
            "entity": dict(entity) if entity else None
        }


@app.post("/resolve-to-vault")
async def resolve_canonical_to_vault(uris: List[str]):
    """
    Resolve canonical URIs to vault paths for wikilink generation.

    Given a list of canonical entity URIs, returns the corresponding vault paths
    that can be used to create wikilinks like [[People/Clare Attwell]].

    Example:
        POST /resolve-to-vault
        ["orn:personal-koi.entity:person-clare-attwell-abc123", ...]

        Returns:
        {
            "mappings": [
                {
                    "canonical_uri": "orn:personal-koi.entity:person-clare-attwell-abc123",
                    "vault_path": "People/Clare Attwell.md",
                    "name": "Clare Attwell",
                    "wikilink": "[[People/Clare Attwell]]"
                }
            ],
            "not_found": []
        }
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    mappings = []
    not_found = []

    async with db_pool.acquire() as conn:
        for uri in uris:
            row = await conn.fetchrow("""
                SELECT vault_rid, vault_path, canonical_uri, entity_type, name
                FROM entity_rid_mappings
                WHERE canonical_uri = $1
                  AND COALESCE(visibility_scope, 'public') != 'node_private'
                LIMIT 1
            """, uri)

            if row:
                # Generate wikilink from vault path
                vault_path = row['vault_path']
                # Remove .md extension and use as wikilink
                wikilink_path = vault_path.replace('.md', '') if vault_path.endswith('.md') else vault_path

                mappings.append({
                    "canonical_uri": row['canonical_uri'],
                    "vault_path": row['vault_path'],
                    "name": row['name'],
                    "entity_type": row['entity_type'],
                    "wikilink": f"[[{wikilink_path}]]"
                })
            else:
                not_found.append(uri)

    return {
        "mappings": mappings,
        "not_found": not_found,
        "resolved": len(mappings),
        "total": len(uris)
    }


class ContextualCandidatesRequest(BaseModel):
    """Request for contextual entity candidates based on meeting context"""
    project: Optional[str] = None
    attendees: Optional[List[str]] = None
    topics: Optional[List[str]] = None
    document_rid: Optional[str] = None  # Current document being processed
    entity_types: List[str] = Field(default_factory=lambda: ["Person"])  # Entity types to return


async def get_contextual_candidates_internal(
    conn: asyncpg.Connection,
    project: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    attendee_uris: Optional[List[str]] = None,
    topics: Optional[List[str]] = None,
    document_rid: Optional[str] = None,
    entity_types: Optional[List[str]] = None
) -> dict:
    """
    Internal helper for finding contextual entity candidates.

    Returns: {"candidates": [...], "related_documents": [...], "context_types": [...]}
    """
    if entity_types is None:
        entity_types = ["Person"]

    candidates = {}  # uri -> candidate info
    related_docs = set()
    context_types = []

    # Strategy 1: Find meetings linked to the same project entity
    if project:
        project_normalized = project.lower().strip()
        project_docs = await conn.fetch("""
            SELECT DISTINCT del.document_rid
            FROM document_entity_links del
            JOIN entity_registry er ON del.entity_uri = er.fuseki_uri
            WHERE er.entity_type = 'Project'
              AND NOT er.node_private
              AND (LOWER(er.entity_text) LIKE $1
                   OR LOWER(er.normalized_text) LIKE $1)
        """, f"%{project_normalized}%")

        for row in project_docs:
            related_docs.add(row['document_rid'])
        if project_docs:
            context_types.append("project")

    # Strategy 2a: Find meetings by attendee URIs (most precise)
    if attendee_uris:
        uri_docs = await conn.fetch("""
            SELECT DISTINCT document_rid
            FROM document_entity_links
            WHERE entity_uri = ANY($1)
        """, attendee_uris)
        for row in uri_docs:
            related_docs.add(row['document_rid'])
        if uri_docs:
            context_types.append("attendee_uris")

    # Strategy 2b: Find meetings with common attendees (by name)
    if attendees:
        for attendee in attendees:
            attendee_normalized = attendee.lower().strip()
            attendee_docs = await conn.fetch("""
                SELECT DISTINCT del.document_rid
                FROM document_entity_links del
                JOIN entity_registry er ON del.entity_uri = er.fuseki_uri
                WHERE er.entity_type = 'Person'
                  AND NOT er.node_private
                  AND (LOWER(er.entity_text) LIKE $1
                       OR LOWER(er.normalized_text) LIKE $1)
            """, f"%{attendee_normalized}%")

            for row in attendee_docs:
                related_docs.add(row['document_rid'])
        if attendees and related_docs:
            context_types.append("attendees")

    # Strategy 3: Find meetings with similar topics (linked Concept entities)
    if topics:
        for topic in topics:
            topic_normalized = topic.lower().strip()
            topic_docs = await conn.fetch("""
                SELECT DISTINCT del.document_rid
                FROM document_entity_links del
                JOIN entity_registry er ON del.entity_uri = er.fuseki_uri
                WHERE er.entity_type = 'Concept'
                  AND NOT er.node_private
                  AND (LOWER(er.entity_text) LIKE $1
                       OR LOWER(er.normalized_text) LIKE $1)
            """, f"%{topic_normalized}%")

            for row in topic_docs:
                related_docs.add(row['document_rid'])
        if topics and related_docs:
            context_types.append("topics")

    # Exclude current document from related docs
    if document_rid and document_rid in related_docs:
        related_docs.discard(document_rid)

    # Get entities of requested types from related documents
    if related_docs:
        related_docs_list = list(related_docs)
        entities = await conn.fetch("""
            SELECT DISTINCT er.fuseki_uri, er.entity_text, er.entity_type,
                   er.normalized_text, er.phonetic_code, del.document_rid
            FROM entity_registry er
            JOIN document_entity_links del ON er.fuseki_uri = del.entity_uri
            WHERE er.entity_type = ANY($1)
              AND NOT er.node_private
              AND del.document_rid = ANY($2)
        """, entity_types, related_docs_list)

        for entity in entities:
            uri = entity['fuseki_uri']
            if uri not in candidates:
                candidates[uri] = {
                    "name": entity['entity_text'],
                    "uri": uri,
                    "normalized_name": entity['normalized_text'],
                    "entity_type": entity['entity_type'],
                    "phonetic_code": entity['phonetic_code'],
                    "source_documents": []
                }
            candidates[uri]["source_documents"].append(entity['document_rid'])

    # Also include entities from vault registry (registered but not yet linked)
    # Only for Person type to keep backward compatibility
    if "Person" in entity_types:
        vault_people = await conn.fetch("""
            SELECT DISTINCT er.fuseki_uri, er.entity_text, er.normalized_text,
                   er.phonetic_code, erm.vault_path
            FROM entity_registry er
            LEFT JOIN entity_rid_mappings erm ON er.fuseki_uri = erm.canonical_uri
            WHERE er.entity_type = 'Person'
              AND NOT er.node_private
              AND erm.vault_path IS NOT NULL
              AND er.fuseki_uri NOT IN (
                  SELECT entity_uri FROM document_entity_links
              )
            LIMIT 50
        """)

        for person in vault_people:
            uri = person['fuseki_uri']
            if uri not in candidates:
                candidates[uri] = {
                    "name": person['entity_text'],
                    "uri": uri,
                    "normalized_name": person['normalized_text'],
                    "entity_type": "Person",
                    "phonetic_code": person['phonetic_code'],
                    "source_documents": [],
                    "vault_path": person['vault_path']
                }

    return {
        "candidates": list(candidates.values()),
        "related_documents": list(related_docs),
        "context_types": context_types
    }


async def get_contextual_entity_candidates(
    conn: asyncpg.Connection,
    label: str,
    entity_type: str,
    associated_people: List[str],
    context: Optional[ResolutionContext] = None
) -> List[dict]:
    """
    Find entity candidates that co-occur with given people.

    Works for any entity type (Person, Organization, Project, Location, Concept).
    Uses unified scoring formula with phonetic boost for types with phonetic_matching=true.
    Now includes relationship-based relevance scoring when context includes project/organizations.

    Args:
        conn: Database connection
        label: The entity label to match against
        entity_type: Type of entity to search for
        associated_people: List of people names that should co-occur
        context: Optional resolution context with project/organizations for relationship checking

    Returns:
        List of scored candidate entities
    """
    target_normalized = normalize_entity_text(label)

    # Get schema-driven config for this entity type
    schema = get_schema_for_type(entity_type)

    # Exclude self from associated_people (prevents circular context)
    people = [
        p.strip() for p in associated_people
        if p.strip() and normalize_entity_text(p.strip()) != target_normalized
    ]
    people = list(set(people))  # Dedupe

    # Cap at 10 to avoid huge ANY() lists if extraction gets noisy
    if len(people) > 10:
        logger.warning(f"Contextual: truncating {len(people)} associated_people to 10")
        people = people[:10]

    # Schema-driven minimum people requirements
    min_people = schema.min_context_people

    if len(people) < min_people:
        logger.info(f"Contextual: need {min_people} associated people for {entity_type}, got {len(people)}")
        return []

    # Resolve people names to URIs
    # For 1-person case: require UNIQUE resolution (1 URI only)
    # For 2+ people: collect all URIs
    resolved_uris = []
    for person in people:
        rows = await conn.fetch("""
            SELECT fuseki_uri FROM entity_registry
            WHERE entity_type = 'Person' AND normalized_text = $1
        """, normalize_entity_text(person))

        if len(people) == 1:
            # Single associated person: require unique resolution
            if len(rows) != 1:
                logger.info(f"Contextual: '{person}' resolves to {len(rows)} URIs, "
                           f"need exactly 1 for single-person context (blocking)")
                return []  # Ambiguous or not found
            resolved_uris.append(rows[0]['fuseki_uri'])
        else:
            # Multiple associated people: collect all URIs
            for row in rows:
                resolved_uris.append(row['fuseki_uri'])

    if len(resolved_uris) < min_people:
        logger.info(f"Contextual: resolved {len(resolved_uris)} URIs, need {min_people}")
        return []

    # Call internal helper with URIs
    response = await get_contextual_candidates_internal(
        conn,
        attendee_uris=resolved_uris,
        entity_types=[entity_type]
    )

    # Compute query's phonetic code (for types with phonetic_matching enabled)
    query_phonetic = None
    if schema.phonetic_matching:
        first_token = get_first_significant_token(target_normalized, schema.phonetic_stopwords)
        query_phonetic = get_phonetic_code(first_token)

    # Score candidates
    scored = []
    for candidate in response.get("candidates", []):
        candidate_normalized = candidate.get("normalized_name", normalize_entity_text(candidate["name"]))
        name_sim = jaro_winkler_similarity(target_normalized, candidate_normalized)
        doc_count = len(candidate.get("source_documents", []))

        # Phonetic bonus for types with phonetic_matching enabled (schema-driven)
        phonetic_bonus = 0.0
        phonetic_match = False
        if schema.phonetic_matching and query_phonetic:
            candidate_phonetic = candidate.get("phonetic_code")
            # Guard: if phonetic_code missing, compute on the fly
            if candidate_phonetic is None:
                candidate_first_token = get_first_significant_token(candidate_normalized, schema.phonetic_stopwords)
                candidate_phonetic = get_phonetic_code(candidate_first_token)
            if phonetic_codes_match(query_phonetic, candidate_phonetic):
                phonetic_bonus = 1.0
                phonetic_match = True

        # Minimum fuzzy threshold (relaxed if phonetic match)
        min_fuzzy = 0.4 if phonetic_bonus > 0 else 0.5
        if name_sim >= min_fuzzy:
            scored.append({
                **candidate,
                "name_similarity": name_sim,
                "phonetic_match": phonetic_match,
                "doc_count": doc_count
            })

    # Combined scoring (unified formula)
    # Stable doc_score normalization: min(doc_count / 3, 1.0)
    for c in scored:
        doc_score = min(c["doc_count"] / 3.0, 1.0)
        phonetic_score = 0.2 if c.get("phonetic_match") else 0.0

        # Unified formula: 0.5 * fuzzy + 0.2 * phonetic + 0.3 * doc
        c["combined_score"] = 0.5 * c["name_similarity"] + phonetic_score + 0.3 * doc_score

    # Relationship-based context relevance scoring
    # Only check if context includes project or organizations
    if context and (context.project or context.organizations):
        for c in scored:
            candidate_uri = c.get("uri")
            if not candidate_uri:
                continue

            # Gate context boost behind name similarity floor
            # Prevents weak name matches (e.g. "Regen Builder Lab" vs "Regen AI")
            # from being pushed over threshold by context alone
            min_name_sim_for_boost = 0.80 if c.get("phonetic_match") else 0.90
            if c["name_similarity"] < min_name_sim_for_boost:
                continue  # Skip boost for weak name matches

            relevance = await check_context_relevance(conn, candidate_uri, context)

            if relevance.signal == RelevanceSignal.POSITIVE:
                c["combined_score"] += relevance.score  # Boost
                c["relevance_detail"] = relevance.details
                logger.debug(f"Relevance POSITIVE for {c['name']}: {relevance.details}")

            elif relevance.signal == RelevanceSignal.NEGATIVE:
                # CRITICAL: Phonetic match alone is NOT enough to bypass penalty
                # Paul→Polly is a phonetic match, but Polly has no Regen/Gaia relationships
                # Require phonetic + high name similarity (>0.9) OR phonetic + semantic match
                has_strong_phonetic = (
                    c.get('phonetic_match') and
                    c.get('name_similarity', 0) >= 0.9  # "Sean"→"Shawn" = 0.93
                )

                if not has_strong_phonetic:
                    c["combined_score"] += relevance.score  # Penalty (negative value)
                    c["relevance_detail"] = relevance.details
                    logger.debug(f"Relevance NEGATIVE for {c['name']}: {relevance.details}")

            elif relevance.signal == RelevanceSignal.UNKNOWN:
                # Fallback to document co-occurrence
                fallback = await check_fallback_relevance(conn, candidate_uri, context)
                if fallback > 0:
                    c["combined_score"] += fallback
                    c["relevance_detail"] = f"doc co-occurrence (+{fallback:.2f})"

    # Clamp scores to valid confidence range [0, 1.0]
    for c in scored:
        c["combined_score"] = min(c["combined_score"], 1.0)

    # Short-name guard with explicit bypass conditions
    has_context = len(resolved_uris) > 0  # Context is present if we got here
    for c in scored:
        if len(c["name"]) < 8:
            # Bypass guard if phonetic match (strong evidence despite short name)
            if c.get("phonetic_match"):
                continue  # Allow short names with phonetic match
            # Bypass guard if: low min_context (like Person) + context present + high fuzzy
            if schema.min_context_people == 1 and has_context and c["name_similarity"] >= 0.85:
                continue  # Allow short names with high fuzzy + context
            # Otherwise apply strict guard
            if c["name_similarity"] < 0.7 or c["doc_count"] < 2:
                c["combined_score"] = 0  # Disqualify short names with weak signals

    scored = [c for c in scored if c["combined_score"] > 0]
    scored.sort(key=lambda x: -x["combined_score"])

    logger.info(f"Contextual {entity_type} candidates for '{label}': {len(scored)} candidates found")
    for c in scored[:3]:  # Log top 3
        phonetic_str = f", phonetic={c.get('phonetic_match', False)}" if schema.phonetic_matching else ""
        logger.info(f"  - {c['name']}: name_sim={c['name_similarity']:.3f}, "
                   f"docs={c['doc_count']}, combined={c['combined_score']:.3f}{phonetic_str}")

    return scored[:10]


# Backward compatibility alias
async def get_contextual_org_candidates(
    conn: asyncpg.Connection,
    label: str,
    associated_people: List[str]
) -> List[dict]:
    """Backward compatible wrapper - use get_contextual_entity_candidates instead."""
    return await get_contextual_entity_candidates(conn, label, 'Organization', associated_people)


@app.post("/get-contextual-candidates")
async def get_contextual_candidates(request: ContextualCandidatesRequest):
    """
    Get contextual entity candidates based on related meetings.

    This endpoint finds entities from related meetings that share:
    - The same project
    - Common attendees
    - Similar topics

    Use case: When processing a meeting that mentions "Sean", this endpoint
    can return "Shawn Anderson" as a candidate if they attended other meetings
    for the same project.

    Example:
        POST /get-contextual-candidates
        {"project": "GLOTCHA", "attendees": ["Mehul Patel"], "entity_types": ["Person"]}

        Returns:
        {
            "candidates": [
                {"name": "Shawn Anderson", "uri": "orn:...", "source_documents": [...]}
            ],
            "related_documents": [...],
            "context_types": ["project"]
        }
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        result = await get_contextual_candidates_internal(
            conn,
            project=request.project,
            attendees=request.attendees,
            topics=request.topics,
            document_rid=request.document_rid,
            entity_types=request.entity_types
        )

    return {
        **result,
        "candidate_count": len(result["candidates"]),
        "related_document_count": len(result["related_documents"])
    }


# =============================================================================
# Relationship Endpoints
# =============================================================================

class SyncRelationshipsRequest(BaseModel):
    """Request to sync relationships from a vault file"""
    vault_path: str  # e.g., "People/Shawn Anderson.md"
    entity_uri: str  # Canonical URI of the entity
    frontmatter: Dict[str, Any]  # YAML frontmatter dict


@app.post("/sync-relationships")
async def sync_relationships_endpoint(request: SyncRelationshipsRequest):
    """
    Sync relationships from vault YAML frontmatter to the database.

    This endpoint:
    1. Deletes existing relationships from this file (replace-all strategy)
    2. Parses YAML fields (affiliation, founder, knows, etc.)
    3. Resolves targets to entity URIs
    4. Stores resolved relationships in entity_relationships
    5. Stores unresolved targets in pending_relationships

    Use Case: Backfill relationships for existing vault entities.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            stats = await sync_vault_relationships(
                conn,
                request.vault_path,
                request.entity_uri,
                request.frontmatter
            )

            # Enqueue relationship changes to TerminusDB outbox
            if stats and TERMINUSDB_ENABLED:
                await _enqueue_relationship_outbox(
                    conn, request.entity_uri, request.vault_path)

    return {
        "success": True,
        "vault_path": request.vault_path,
        "stats": stats
    }


@app.get("/relationships/{entity_uri:path}")
async def get_relationships_endpoint(
    entity_uri: str,
    predicate: Optional[str] = None,
    direction: Literal["incoming", "outgoing", "both"] = "both",
):
    """
    Get all relationships for an entity.

    Query Parameters:
        predicate: Optional filter by predicate (e.g., 'affiliated_with')
        direction: "both" (default), "incoming", or "outgoing"

    Returns relationships where the entity is subject and/or object.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Reject queries for node_private entities
        is_private = await conn.fetchval(
            "SELECT node_private FROM entity_registry WHERE fuseki_uri = $1",
            entity_uri
        )
        if is_private:
            raise HTTPException(status_code=404, detail="Entity not found")

        relationships = await get_entity_relationships(conn, entity_uri, predicate, direction)

        # Enrich with entity names, filtering out node_private neighbors
        enriched = []
        for rel in relationships:
            # Get subject name
            subject_row = await conn.fetchrow("""
                SELECT entity_text, entity_type, node_private FROM entity_registry
                WHERE fuseki_uri = $1
            """, rel['subject_uri'])

            # Get object name
            object_row = await conn.fetchrow("""
                SELECT entity_text, entity_type, node_private FROM entity_registry
                WHERE fuseki_uri = $1
            """, rel['object_uri'])

            # Skip relationships where either endpoint is node_private
            if (subject_row and subject_row['node_private']) or (object_row and object_row['node_private']):
                continue

            enriched.append({
                **rel,
                "subject_name": subject_row['entity_text'] if subject_row else None,
                "subject_type": subject_row['entity_type'] if subject_row else None,
                "object_name": object_row['entity_text'] if object_row else None,
                "object_type": object_row['entity_type'] if object_row else None,
            })

    return {
        "entity_uri": entity_uri,
        "relationships": enriched,
        "count": len(enriched)
    }


@app.get("/relationship-stats")
async def get_relationship_stats():
    """Get relationship statistics"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Total counts
        total_relationships = await conn.fetchval("SELECT COUNT(*) FROM entity_relationships")
        total_pending = await conn.fetchval("SELECT COUNT(*) FROM pending_relationships")

        # By predicate
        by_predicate = await conn.fetch("""
            SELECT predicate, COUNT(*) as count
            FROM entity_relationships
            GROUP BY predicate
            ORDER BY count DESC
        """)

        # Top pending (unresolved labels)
        top_pending = await conn.fetch("""
            SELECT raw_unknown_label, predicate, unknown_side, COUNT(*) as count
            FROM pending_relationships
            GROUP BY raw_unknown_label, predicate, unknown_side
            ORDER BY count DESC
            LIMIT 20
        """)

        return {
            "total_relationships": total_relationships,
            "total_pending": total_pending,
            "by_predicate": {r['predicate']: r['count'] for r in by_predicate},
            "top_pending": [dict(r) for r in top_pending]
        }


# =============================================================================
# Session Search Endpoints (for Claude Code session memory)
# =============================================================================

class SearchSessionsRequest(BaseModel):
    """Request to search Claude Code sessions"""
    query: str
    limit: int = 10
    session_id: Optional[str] = None  # Filter to specific session
    include_context: bool = True  # Include surrounding chunks


class SessionSearchResult(BaseModel):
    """A single session search result"""
    session_id: str
    session_rid: str
    chunk_index: int
    chunk_text: str
    similarity: Optional[float] = None
    summary: Optional[str] = None
    first_prompt: Optional[str] = None
    timestamp: Optional[str] = None


@app.post("/search-sessions")
async def search_sessions(request: SearchSessionsRequest):
    """
    Search Claude Code session transcripts.

    Performs semantic search over indexed session chunks.
    Returns matching chunks with session metadata.

    Example:
        POST /search-sessions
        {"query": "entity resolution", "limit": 10}
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    results = []

    async with db_pool.acquire() as conn:
        # Check if session_chunks table exists
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'session_chunks'
            )
        """)

        if not table_exists:
            return {
                "results": [],
                "count": 0,
                "message": "Session chunks table not found. Run the session sensor first."
            }

        # Check if we have embeddings
        has_embeddings = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM session_chunks WHERE embedding IS NOT NULL LIMIT 1
            )
        """)

        if has_embeddings and embedding_provider and ENABLE_SEMANTIC_MATCHING:
            # Semantic search with embeddings
            query_embedding = await generate_embedding(request.query)

            if query_embedding:
                if request.session_id:
                    # Search within specific session
                    rows = await conn.fetch("""
                        SELECT sc.session_id, sc.session_rid, sc.chunk_index,
                               sc.chunk_text, sc.timestamp,
                               1 - (sc.embedding <=> $1::vector) AS similarity,
                               sil.summary, sil.first_prompt
                        FROM session_chunks sc
                        LEFT JOIN session_ingestion_log sil ON sc.session_id = sil.session_id
                        WHERE sc.session_id = $2
                          AND sc.embedding IS NOT NULL
                        ORDER BY similarity DESC
                        LIMIT $3
                    """, str(query_embedding), request.session_id, request.limit)
                else:
                    # Search all sessions
                    rows = await conn.fetch("""
                        SELECT sc.session_id, sc.session_rid, sc.chunk_index,
                               sc.chunk_text, sc.timestamp,
                               1 - (sc.embedding <=> $1::vector) AS similarity,
                               sil.summary, sil.first_prompt
                        FROM session_chunks sc
                        LEFT JOIN session_ingestion_log sil ON sc.session_id = sil.session_id
                        WHERE sc.embedding IS NOT NULL
                        ORDER BY similarity DESC
                        LIMIT $2
                    """, str(query_embedding), request.limit)

                for row in rows:
                    results.append({
                        "session_id": row['session_id'],
                        "session_rid": row['session_rid'],
                        "chunk_index": row['chunk_index'],
                        "chunk_text": row['chunk_text'][:2000],  # Limit text size
                        "similarity": float(row['similarity']) if row['similarity'] else None,
                        "summary": row['summary'],
                        "first_prompt": row['first_prompt'][:200] if row['first_prompt'] else None,
                        "timestamp": row['timestamp'].isoformat() if row['timestamp'] else None
                    })
        else:
            # Fallback: text search (basic ILIKE)
            search_pattern = f"%{request.query}%"

            if request.session_id:
                rows = await conn.fetch("""
                    SELECT sc.session_id, sc.session_rid, sc.chunk_index,
                           sc.chunk_text, sc.timestamp,
                           sil.summary, sil.first_prompt
                    FROM session_chunks sc
                    LEFT JOIN session_ingestion_log sil ON sc.session_id = sil.session_id
                    WHERE sc.session_id = $1
                      AND sc.chunk_text ILIKE $2
                    ORDER BY sc.chunk_index
                    LIMIT $3
                """, request.session_id, search_pattern, request.limit)
            else:
                rows = await conn.fetch("""
                    SELECT sc.session_id, sc.session_rid, sc.chunk_index,
                           sc.chunk_text, sc.timestamp,
                           sil.summary, sil.first_prompt
                    FROM session_chunks sc
                    LEFT JOIN session_ingestion_log sil ON sc.session_id = sil.session_id
                    WHERE sc.chunk_text ILIKE $1
                    ORDER BY sil.last_ingested_at DESC, sc.chunk_index
                    LIMIT $2
                """, search_pattern, request.limit)

            for row in rows:
                results.append({
                    "session_id": row['session_id'],
                    "session_rid": row['session_rid'],
                    "chunk_index": row['chunk_index'],
                    "chunk_text": row['chunk_text'][:2000],
                    "similarity": None,  # No similarity for text search
                    "summary": row['summary'],
                    "first_prompt": row['first_prompt'][:200] if row['first_prompt'] else None,
                    "timestamp": row['timestamp'].isoformat() if row['timestamp'] else None
                })

    return {
        "results": results,
        "count": len(results),
        "query": request.query,
        "search_type": "semantic" if has_embeddings and embedding_provider else "text"
    }


@app.get("/session-stats")
async def get_session_stats():
    """Get statistics about indexed Claude Code sessions."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Check if tables exist
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'session_ingestion_log'
            )
        """)

        if not table_exists:
            return {
                "indexed": False,
                "message": "Session tables not found. Run the session sensor first."
            }

        total_sessions = await conn.fetchval(
            "SELECT COUNT(*) FROM session_ingestion_log"
        )
        total_chunks = await conn.fetchval(
            "SELECT COUNT(*) FROM session_chunks"
        )
        chunks_with_embeddings = await conn.fetchval(
            "SELECT COUNT(*) FROM session_chunks WHERE embedding IS NOT NULL"
        )

        recent_sessions = await conn.fetch("""
            SELECT session_id, summary, first_prompt, message_count, chunk_count, last_ingested_at
            FROM session_ingestion_log
            ORDER BY last_ingested_at DESC
            LIMIT 5
        """)

        return {
            "indexed": True,
            "total_sessions": total_sessions,
            "total_chunks": total_chunks,
            "chunks_with_embeddings": chunks_with_embeddings,
            "embedding_coverage": f"{(chunks_with_embeddings / total_chunks * 100):.1f}%" if total_chunks > 0 else "0%",
            "recent_sessions": [
                {
                    "session_id": r['session_id'],
                    "summary": r['summary'],
                    "first_prompt": r['first_prompt'][:100] if r['first_prompt'] else None,
                    "message_count": r['message_count'],
                    "chunk_count": r['chunk_count'],
                    "last_ingested_at": r['last_ingested_at'].isoformat() if r['last_ingested_at'] else None
                }
                for r in recent_sessions
            ]
        }


@app.get("/session-tools")
async def get_session_tools(
    tool: Optional[str] = None,
    mcp_server: Optional[str] = None,
    limit: int = 20
):
    """
    Query sessions by tool usage.

    Examples:
        GET /session-tools?tool=Bash
        GET /session-tools?mcp_server=personal-koi
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Check if table exists
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'session_tool_usage'
            )
        """)

        if not table_exists:
            return {
                "results": [],
                "message": "Session tool usage table not found. Re-run sensor to extract metadata."
            }

        if tool:
            # Find sessions using a specific tool
            rows = await conn.fetch("""
                SELECT stu.session_id, stu.tool_name, stu.call_count,
                       sil.summary, sil.first_prompt, sil.last_ingested_at
                FROM session_tool_usage stu
                JOIN session_ingestion_log sil ON stu.session_id = sil.session_id
                WHERE stu.tool_name ILIKE $1
                ORDER BY stu.call_count DESC, sil.last_ingested_at DESC
                LIMIT $2
            """, f"%{tool}%", limit)
        elif mcp_server:
            # Find sessions using a specific MCP server
            rows = await conn.fetch("""
                SELECT stu.session_id, stu.tool_name, stu.call_count, stu.mcp_server,
                       sil.summary, sil.first_prompt, sil.last_ingested_at
                FROM session_tool_usage stu
                JOIN session_ingestion_log sil ON stu.session_id = sil.session_id
                WHERE stu.mcp_server ILIKE $1
                ORDER BY sil.last_ingested_at DESC
                LIMIT $2
            """, f"%{mcp_server}%", limit)
        else:
            # Return overall tool usage stats
            rows = await conn.fetch("""
                SELECT tool_name, SUM(call_count) as total_calls,
                       COUNT(DISTINCT session_id) as session_count,
                       is_mcp, mcp_server
                FROM session_tool_usage
                GROUP BY tool_name, is_mcp, mcp_server
                ORDER BY total_calls DESC
                LIMIT $1
            """, limit)

            return {
                "tool_stats": [
                    {
                        "tool_name": r['tool_name'],
                        "total_calls": r['total_calls'],
                        "session_count": r['session_count'],
                        "is_mcp": r['is_mcp'],
                        "mcp_server": r['mcp_server']
                    }
                    for r in rows
                ]
            }

        return {
            "results": [
                {
                    "session_id": r['session_id'],
                    "tool_name": r['tool_name'],
                    "call_count": r['call_count'],
                    "mcp_server": r.get('mcp_server'),
                    "summary": r['summary'],
                    "first_prompt": r['first_prompt'][:100] if r['first_prompt'] else None,
                    "last_ingested_at": r['last_ingested_at'].isoformat() if r['last_ingested_at'] else None
                }
                for r in rows
            ],
            "count": len(rows),
            "filter": {"tool": tool, "mcp_server": mcp_server}
        }


@app.get("/session-files")
async def get_session_files(
    path_contains: Optional[str] = None,
    limit: int = 20
):
    """
    Query sessions by files accessed.

    Examples:
        GET /session-files?path_contains=koi-processor
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        if path_contains:
            # Find sessions that accessed files matching pattern
            rows = await conn.fetch("""
                SELECT session_id, summary, first_prompt, files_accessed, last_ingested_at
                FROM session_ingestion_log
                WHERE files_accessed IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM unnest(files_accessed) f WHERE f ILIKE $1
                  )
                ORDER BY last_ingested_at DESC
                LIMIT $2
            """, f"%{path_contains}%", limit)
        else:
            # Return sessions with most files accessed
            rows = await conn.fetch("""
                SELECT session_id, summary, first_prompt,
                       files_accessed, array_length(files_accessed, 1) as file_count,
                       last_ingested_at
                FROM session_ingestion_log
                WHERE files_accessed IS NOT NULL
                  AND array_length(files_accessed, 1) > 0
                ORDER BY array_length(files_accessed, 1) DESC
                LIMIT $1
            """, limit)

        return {
            "results": [
                {
                    "session_id": r['session_id'],
                    "summary": r['summary'],
                    "first_prompt": r['first_prompt'][:100] if r['first_prompt'] else None,
                    "files_accessed": r['files_accessed'][:20] if r['files_accessed'] else [],
                    "file_count": len(r['files_accessed']) if r['files_accessed'] else 0,
                    "last_ingested_at": r['last_ingested_at'].isoformat() if r['last_ingested_at'] else None
                }
                for r in rows
            ],
            "count": len(rows),
            "filter": {"path_contains": path_contains}
        }


# =============================================================================
# Knowledge Base Search (Emails, Vault, etc.)
# =============================================================================

BGE_SERVER_URL = os.getenv('BGE_SERVER_URL', 'http://localhost:8091/encode')


class SearchRequest(BaseModel):
    """Request for knowledge base search"""
    query: str
    limit: int = 10
    source: Optional[str] = None  # Filter by source: 'email', 'vault', etc.
    include_chunks: bool = False  # Also search chunk-level embeddings


class SearchResult(BaseModel):
    """Single search result"""
    rid: str
    title: Optional[str] = None
    content_preview: str
    similarity: float
    source: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


async def get_bge_embedding(text: str) -> Optional[List[float]]:
    """Get embedding from local BGE server."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                BGE_SERVER_URL,
                json={"text": text}
            )
            if response.status_code == 200:
                return response.json().get("embedding")
            else:
                logger.warning(f"BGE server error: {response.status_code}")
                return None
    except Exception as e:
        logger.error(f"BGE embedding error: {e}")
        return None


@app.post("/search")
async def search_knowledge_base(request: SearchRequest):
    """
    Search the personal knowledge base (emails, vault notes, etc.).

    Performs semantic search using BGE embeddings over koi_memories.

    Args:
        query: Search query text
        limit: Max results (default 10)
        source: Filter by source ('email', 'vault', etc.)
        include_chunks: Also search chunk-level embeddings

    Example:
        POST /search
        {"query": "hackathon ethereum", "limit": 10, "source": "email"}
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    # Generate query embedding using BGE
    query_embedding = await get_bge_embedding(request.query)

    results = []
    search_type = "text"  # fallback

    async with db_pool.acquire() as conn:
        if query_embedding:
            search_type = "semantic"
            embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'

            # Build query with optional source filter
            if request.source == 'email':
                source_filter = "AND m.source_sensor = 'email-sensor'"
            elif request.source == 'vault':
                source_filter = "AND m.source_sensor = 'obsidian-sensor'"
            elif request.source:
                source_filter = f"AND m.source_sensor = '{request.source}'"
            else:
                source_filter = ""

            # Search doc-level embeddings
            query = f"""
                SELECT
                    m.rid,
                    m.content->>'title' as title,
                    LEFT(m.content->>'text', 500) as content_preview,
                    1 - (e.dim_1024 <=> $1::vector) as similarity,
                    m.source_sensor,
                    m.metadata,
                    m.created_at
                FROM koi_memories m
                JOIN koi_embeddings e ON e.memory_id = m.id
                WHERE e.dim_1024 IS NOT NULL
                {source_filter}
                ORDER BY e.dim_1024 <=> $1::vector
                LIMIT $2
            """

            rows = await conn.fetch(query, embedding_str, request.limit)

            for row in rows:
                # Handle metadata - asyncpg returns JSONB as dict already
                metadata = row['metadata']
                if metadata is None:
                    metadata = {}
                elif isinstance(metadata, str):
                    import json as json_module
                    metadata = json_module.loads(metadata)

                result = {
                    "rid": row['rid'],
                    "title": row['title'],
                    "content_preview": row['content_preview'],
                    "similarity": float(row['similarity']) if row['similarity'] else 0,
                    "source": row['source_sensor'],
                    "metadata": metadata,
                }

                # Add email-specific metadata if available
                if row['source_sensor'] == 'email-sensor':
                    email_meta = await conn.fetchrow("""
                        SELECT subject, from_name, from_address, date_sent
                        FROM email_metadata
                        WHERE rid = $1
                    """, row['rid'])

                    if email_meta:
                        result['email'] = {
                            "subject": email_meta['subject'],
                            "from_name": email_meta['from_name'],
                            "from_address": email_meta['from_address'],
                            "date_sent": email_meta['date_sent'].isoformat() if email_meta['date_sent'] else None
                        }

                results.append(result)

            # Optionally search chunks for more coverage
            if request.include_chunks and len(results) < request.limit:
                chunk_query = f"""
                    SELECT DISTINCT ON (c.document_rid)
                        c.document_rid as rid,
                        m.content->>'title' as title,
                        LEFT(c.content->>'text', 500) as content_preview,
                        1 - (c.embedding <=> $1::vector) as similarity,
                        m.source_sensor,
                        m.metadata
                    FROM koi_memory_chunks c
                    JOIN koi_memories m ON m.rid = c.document_rid
                    WHERE c.embedding IS NOT NULL
                      AND c.document_rid NOT IN (SELECT rid FROM unnest($3::text[]) as rid)
                    {source_filter.replace('m.source_sensor', 'm.source_sensor')}
                    ORDER BY c.document_rid, c.embedding <=> $1::vector
                    LIMIT $2
                """

                existing_rids = [r['rid'] for r in results]
                chunk_rows = await conn.fetch(
                    chunk_query,
                    embedding_str,
                    request.limit - len(results),
                    existing_rids
                )

                for row in chunk_rows:
                    chunk_metadata = row['metadata'] if row['metadata'] else {}
                    results.append({
                        "rid": row['rid'],
                        "title": row['title'],
                        "content_preview": row['content_preview'],
                        "similarity": float(row['similarity']) if row['similarity'] else 0,
                        "source": row['source_sensor'],
                        "metadata": chunk_metadata,
                        "matched_via": "chunk"
                    })

        else:
            # Fallback: text search
            search_type = "text"
            search_pattern = f"%{request.query}%"

            if request.source == 'email':
                source_filter = "AND m.source_sensor = 'email-sensor'"
            elif request.source:
                source_filter = f"AND m.source_sensor = '{request.source}'"
            else:
                source_filter = ""

            query = f"""
                SELECT
                    m.rid,
                    m.content->>'title' as title,
                    LEFT(m.content->>'text', 500) as content_preview,
                    m.source_sensor,
                    m.metadata,
                    m.created_at
                FROM koi_memories m
                WHERE m.content->>'text' ILIKE $1
                {source_filter}
                ORDER BY m.created_at DESC
                LIMIT $2
            """

            rows = await conn.fetch(query, search_pattern, request.limit)

            for row in rows:
                text_metadata = row['metadata'] if row['metadata'] else {}
                results.append({
                    "rid": row['rid'],
                    "title": row['title'],
                    "content_preview": row['content_preview'],
                    "similarity": None,
                    "source": row['source_sensor'],
                    "metadata": text_metadata
                })

    return {
        "results": results,
        "count": len(results),
        "query": request.query,
        "search_type": search_type,
        "source_filter": request.source
    }


@app.get("/search")
async def search_knowledge_base_get(
    q: str,
    limit: int = 10,
    source: Optional[str] = None
):
    """GET version of search for convenience."""
    request = SearchRequest(query=q, limit=limit, source=source)
    return await search_knowledge_base(request)


# =============================================================================
# /query Endpoint (Compatible with regen-koi-mcp)
# =============================================================================

class QueryRequest(BaseModel):
    """Query request compatible with regen-koi-mcp format"""
    query: Optional[str] = None
    question: Optional[str] = None  # Alternative field name
    limit: int = 10
    intent: Optional[str] = None
    source: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    published_from: Optional[str] = None
    published_to: Optional[str] = None


@app.post("/query")
async def query_knowledge_base(request: QueryRequest):
    """
    Query endpoint compatible with regen-koi-mcp format.

    This wraps the /search endpoint to provide compatibility with the MCP client.
    Accepts both 'query' and 'question' parameters.

    Example:
        POST /query
        {"query": "hackathon", "limit": 10, "source": "email"}
    """
    # Use query or question parameter
    query_text = request.query or request.question or ""

    if not query_text or query_text == "warmup":
        # Return empty results for warmup or empty queries
        return {"results": [], "count": 0, "query": query_text}

    # Map source filter
    source = request.source
    if request.filters and request.filters.get('source'):
        source = request.filters['source']

    # Call the search endpoint
    search_request = SearchRequest(
        query=query_text,
        limit=request.limit,
        source=source,
        include_chunks=True  # Include chunks for better coverage
    )

    search_result = await search_knowledge_base(search_request)

    # Transform results to match expected format
    results = []
    for r in search_result.get("results", []):
        result = {
            "rid": r.get("rid"),
            "title": r.get("title") or r.get("email", {}).get("subject") or "Untitled",
            "content": r.get("content_preview", ""),
            "similarity": r.get("similarity", 0),
            "source": r.get("source"),
            "url": None,  # Emails don't have URLs
            "published_at": r.get("email", {}).get("date_sent") if r.get("email") else None,
            "metadata": r.get("metadata", {}),
        }

        # Add email-specific fields if present
        if r.get("email"):
            result["email"] = r["email"]

        results.append(result)

    return {
        "results": results,
        "count": len(results),
        "query": query_text,
        "search_type": search_result.get("search_type", "semantic")
    }


# =============================================================================
# TerminusDB Graph Endpoints
# =============================================================================

GRAPH_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _check_graph_auth(request: Request):
    """Restrict /graph/* endpoints to localhost and WireGuard mesh (10.100.0.0/24)."""
    client_host = request.client.host if request.client else None
    if client_host in GRAPH_ALLOWED_HOSTS:
        return
    # Allow WireGuard mesh subnet
    if client_host and client_host.startswith("10.100.0."):
        return
    raise HTTPException(status_code=403, detail="Graph endpoints restricted to local access")


@app.get("/graph/health")
async def graph_health(request: Request):
    """TerminusDB connection status, schema hash, sync lag metrics."""
    _check_graph_auth(request)
    if not TERMINUSDB_ENABLED:
        return {"terminusdb_enabled": False}

    result = {}
    if terminusdb_adapter:
        result = terminusdb_adapter.health()
    else:
        result = {"terminusdb_reachable": False, "error": "adapter not initialized"}

    # Add outbox metrics from PostgreSQL
    if db_pool:
        async with db_pool.acquire() as conn:
            pending = await conn.fetchval(
                "SELECT COUNT(*) FROM terminusdb_outbox WHERE status = 'pending'")
            dead = await conn.fetchval(
                "SELECT COUNT(*) FROM terminusdb_outbox WHERE status = 'dead_letter'")
            oldest = await conn.fetchval("""
                SELECT EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))
                FROM terminusdb_outbox WHERE status = 'pending'
            """)
            result["pending_outbox_count"] = pending or 0
            result["dead_letter_count"] = dead or 0
            result["oldest_pending_age_s"] = round(oldest, 1) if oldest else None

    return result


@app.get("/graph/conflicts")
async def graph_conflicts(request: Request, limit: int = 50, offset: int = 0):
    """All conflicts (grouped by subject+predicate)."""
    _check_graph_auth(request)
    if not TERMINUSDB_ENABLED or not terminusdb_adapter:
        raise HTTPException(status_code=503, detail="TerminusDB not enabled")

    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    conflicts, total = terminusdb_adapter.get_conflicts(limit=limit, offset=offset)
    return {"conflicts": conflicts, "total": total, "limit": limit, "offset": offset}


@app.get("/graph/conflicts/{entity_rid:path}")
async def graph_conflicts_for_entity(request: Request, entity_rid: str):
    """Conflicts for a specific entity."""
    _check_graph_auth(request)
    if not TERMINUSDB_ENABLED or not terminusdb_adapter:
        raise HTTPException(status_code=503, detail="TerminusDB not enabled")

    conflicts, total = terminusdb_adapter.get_conflicts(entity_rid=entity_rid)
    return {"entity_rid": entity_rid, "conflicts": conflicts, "total": total}


@app.get("/graph/assertions/{entity_rid:path}")
async def graph_assertions(request: Request, entity_rid: str, limit: int = 100, offset: int = 0):
    """All assertions about an entity."""
    _check_graph_auth(request)
    if not TERMINUSDB_ENABLED or not terminusdb_adapter:
        raise HTTPException(status_code=503, detail="TerminusDB not enabled")

    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    assertions, total = terminusdb_adapter.get_assertions(
        entity_rid=entity_rid, limit=limit, offset=offset)
    return {"entity_rid": entity_rid, "assertions": assertions,
            "total": total, "limit": limit, "offset": offset}


# =============================================================================
# Graph Traversal Endpoints
# =============================================================================


@app.get("/graph/neighborhood/{entity_uri:path}", response_model=NeighborhoodResponse)
async def graph_neighborhood(
    request: Request,
    entity_uri: str,
    max_depth: int = 2,
    direction: Literal["incoming", "outgoing", "both"] = "both",
    predicate: Optional[str] = None,
    entity_type: Optional[str] = None,
    max_nodes: int = 200,
    max_edges: int = 1000,
):
    """
    Return the neighborhood graph around an entity via recursive traversal.

    Discovers reachable nodes up to `max_depth` hops, returns nodes + edges
    between them. Safety caps prevent runaway queries.

    - **max_depth**: 1-4 (default 2, silently clamped)
    - **direction**: which edges to traverse ("both", "incoming", "outgoing")
    - **predicate**: only traverse edges with this predicate
    - **entity_type**: post-filter nodes by type (root always included)
    - **max_nodes**: cap on returned nodes (default 200, max 500)
    - **max_edges**: cap on returned edges (default 1000, max 2000)

    When truncated, `total_nodes_discovered` / `total_edges_discovered`
    show the uncapped counts.
    """
    _check_graph_auth(request)

    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Check entity exists
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM entity_registry WHERE fuseki_uri = $1)",
            entity_uri,
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"Entity not found: {entity_uri}")

        from api.graph_queries import get_neighborhood
        result = await get_neighborhood(
            conn, entity_uri, max_depth, direction, predicate,
            entity_type, max_nodes, max_edges,
        )

    return result


@app.get("/graph/shortest-path", response_model=ShortestPathResponse)
async def graph_shortest_path(
    request: Request,
    source: str,
    target: str,
    max_depth: int = 6,
    direction: Literal["incoming", "outgoing", "both"] = "both",
):
    """
    Find the shortest path between two entities via BFS.

    - **source**: URI of the starting entity
    - **target**: URI of the destination entity
    - **max_depth**: maximum hops (1-8, default 6, silently clamped)
    - **direction**: which edges to follow ("both", "incoming", "outgoing")

    When `source == target`, returns `path_length: 0` with an empty steps list.

    Each step shows the edge connecting consecutive nodes. When multiple edges
    exist between two nodes, the highest-confidence edge is chosen (ties broken
    alphabetically by predicate) for deterministic results.
    """
    _check_graph_auth(request)

    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Check both entities exist
        for uri, label in [(source, "source"), (target, "target")]:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM entity_registry WHERE fuseki_uri = $1)",
                uri,
            )
            if not exists:
                raise HTTPException(status_code=404, detail=f"{label.capitalize()} entity not found: {uri}")

        from api.graph_queries import get_shortest_path
        result = await get_shortest_path(conn, source, target, max_depth, direction)

    return result


# =============================================================================
# /chat Endpoint — RAG-powered conversational interface
# =============================================================================

CHAT_LLM_MODEL = os.getenv('CHAT_LLM_MODEL', 'gpt-4o-mini')


# ── B2 GraphRAG: graph-guided retrieval ──────────────────────────────

async def _compute_graph_version_hash(conn) -> str:
    """Compute deterministic graph state hash for cache invalidation."""
    import hashlib as _hashlib
    row = await conn.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM entity_registry) AS ec,
            (SELECT COUNT(*) FROM entity_relationships) AS rc,
            (SELECT MAX(updated_at) FROM entity_registry) AS meu,
            (SELECT GREATEST(MAX(created_at), MAX(updated_at)) FROM entity_relationships) AS mrc
    """)
    state = f"{row['ec']}:{row['rc']}:{row['meu']}:{row['mrc']}"
    return _hashlib.sha256(state.encode()).hexdigest()[:16]


async def _ensure_graph_metrics(conn) -> bool:
    """Ensure entity_graph_metrics is populated and fresh. Returns True if available."""
    try:
        row = await conn.fetchrow(
            "SELECT graph_version, COUNT(*) as cnt FROM entity_graph_metrics GROUP BY graph_version LIMIT 1"
        )
        if not row or row['cnt'] == 0:
            return False
        current_version = await _compute_graph_version_hash(conn)
        return row['graph_version'] == current_version
    except Exception:
        return False


async def _graph_guided_retrieval(
    query: str,
    query_embedding: list,
    conn,
    top_k: int = 10,
) -> tuple:
    """B2 GraphRAG: community-aware, centrality-weighted retrieval.

    Returns (sources, relationships_ctx, doc_chunks, web_sources) matching
    the B1 interface so the LLM prompt builder works unchanged.

    Strategy:
    1. Semantic search for seed entities (same as B1)
    2. Look up community_l1 + betweenness for each seed from entity_graph_metrics
    3. Expand: entities in same L1 community, sorted by betweenness DESC
    4. Follow edges from seeds via entity_relationships (predicate-aware)
    5. Rank: semantic_score * 0.4 + centrality * 0.3 + community_overlap * 0.3
    """
    sources = []
    relationships_ctx = []
    doc_chunks = []
    web_sources = []

    if not query_embedding:
        return sources, relationships_ctx, doc_chunks, web_sources

    embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'

    # ── Step 1: Seed entities via semantic search (like B1, with fallback) ──
    try:
        seed_rows = await conn.fetch("""
            SELECT id, fuseki_uri, entity_text, entity_type, metadata,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM entity_registry
            WHERE embedding IS NOT NULL AND NOT node_private
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """, embedding_str, top_k)
    except (asyncpg.exceptions.UndefinedColumnError,
            asyncpg.exceptions.UndefinedFunctionError,
            asyncpg.exceptions.DataError) as e:
        logger.warning("entity_registry.embedding vector search failed, falling back to text search: %s", e)
        # Fallback: text search when vector column/extension is unavailable
        words = [w for w in query.lower().split() if len(w) >= 3]
        if words:
            conditions = " OR ".join(f"normalized_text ILIKE ${i+1}" for i in range(len(words)))
            match_score = " + ".join(
                f"CASE WHEN normalized_text ILIKE ${i+1} THEN 1 ELSE 0 END"
                for i in range(len(words))
            )
            params = [f"%{w}%" for w in words]
            params.append(top_k)
            seed_rows = await conn.fetch(f"""
                SELECT id, fuseki_uri, entity_text, entity_type, metadata,
                       ({match_score})::float / {len(words)} AS similarity
                FROM entity_registry
                WHERE ({conditions}) AND NOT node_private
                ORDER BY ({match_score}) DESC, created_at DESC
                LIMIT ${len(words)+1}
            """, *params)
        else:
            seed_rows = []

    if not seed_rows:
        return sources, relationships_ctx, doc_chunks, web_sources

    seed_ids = [r['id'] for r in seed_rows]
    seed_uris = [r['fuseki_uri'] for r in seed_rows]
    seed_scores = {r['id']: float(r['similarity']) for r in seed_rows}

    # ── Step 2: Get community + centrality for seeds ──
    metrics_available = await _ensure_graph_metrics(conn)
    seed_communities = {}
    seed_betweenness = {}

    if metrics_available:
        metric_rows = await conn.fetch("""
            SELECT entity_id, community_l1, betweenness
            FROM entity_graph_metrics
            WHERE entity_id = ANY($1)
        """, seed_ids)
        for mr in metric_rows:
            seed_communities[mr['entity_id']] = mr['community_l1']
            seed_betweenness[mr['entity_id']] = mr['betweenness']

    # Determine dominant communities from seeds
    comm_counts = {}
    for eid in seed_ids:
        c = seed_communities.get(eid, -1)
        if c >= 0:
            comm_counts[c] = comm_counts.get(c, 0) + 1
    dominant_communities = sorted(comm_counts, key=comm_counts.get, reverse=True)[:3]

    # ── Step 3: Expand via community — get high-centrality entities in same communities ──
    expanded_entities = {}  # id -> {uri, label, type, score, description}

    if metrics_available and dominant_communities:
        community_rows = await conn.fetch("""
            SELECT
                er.id, er.fuseki_uri, er.entity_text, er.entity_type, er.metadata,
                egm.community_l1, egm.betweenness
            FROM entity_graph_metrics egm
            JOIN entity_registry er ON er.id = egm.entity_id
            WHERE egm.community_l1 = ANY($1) AND NOT er.node_private
            ORDER BY egm.betweenness DESC
            LIMIT $2
        """, dominant_communities, top_k * 2)

        for cr in community_rows:
            eid = cr['id']
            if eid not in expanded_entities:
                expanded_entities[eid] = {
                    'id': eid,
                    'uri': cr['fuseki_uri'],
                    'label': cr['entity_text'],
                    'type': cr['entity_type'],
                    'metadata': cr['metadata'],
                    'betweenness': float(cr['betweenness'] or 0),
                    'community': cr['community_l1'],
                    'semantic_score': seed_scores.get(eid, 0.0),
                }

    # Add seeds that might not be in expanded set
    for row in seed_rows:
        eid = row['id']
        if eid not in expanded_entities:
            meta = row['metadata'] or {}
            if isinstance(meta, str):
                meta = json_module_global.loads(meta)
            expanded_entities[eid] = {
                'id': eid,
                'uri': row['fuseki_uri'],
                'label': row['entity_text'],
                'type': row['entity_type'],
                'metadata': meta,
                'betweenness': seed_betweenness.get(eid, 0.0),
                'community': seed_communities.get(eid, -1),
                'semantic_score': float(row['similarity']),
            }

    # ── Step 4: Composite ranking ──
    # score = semantic * 0.4 + centrality * 0.3 + community_overlap * 0.3
    max_bc = max((e['betweenness'] for e in expanded_entities.values()), default=1.0) or 1.0

    for eid, ent in expanded_entities.items():
        sem = ent['semantic_score']
        bc_norm = ent['betweenness'] / max_bc
        comm_overlap = 1.0 if ent['community'] in dominant_communities else 0.0
        ent['composite_score'] = sem * 0.4 + bc_norm * 0.3 + comm_overlap * 0.3

    # Sort by composite score, take top_k
    ranked = sorted(expanded_entities.values(), key=lambda x: x['composite_score'], reverse=True)[:top_k]

    # Build sources list
    entity_uris = []
    for ent in ranked:
        meta = ent.get('metadata', {})
        if isinstance(meta, str):
            meta = json_module_global.loads(meta)
        description = meta.get('description', '') if isinstance(meta, dict) else ''
        sources.append({
            "uri": ent['uri'],
            "label": ent['label'],
            "entity_type": ent['type'],
            "score": round(ent['composite_score'], 4),
            "description": description,
            "retrieval_mode": "graphrag",
            "community": ent.get('community', -1),
            "betweenness": round(ent.get('betweenness', 0), 6),
        })
        entity_uris.append(ent['uri'])

    # ── Step 5: Relationships — predicate-aware, community-guided ──
    if entity_uris:
        rel_rows = await conn.fetch("""
            WITH RECURSIVE traverse AS (
                SELECT r.subject_uri, r.object_uri, r.predicate, 1 AS depth
                FROM entity_relationships r
                WHERE r.subject_uri = ANY($1) OR r.object_uri = ANY($1)
                UNION
                SELECT r2.subject_uri, r2.object_uri, r2.predicate, t.depth + 1
                FROM traverse t
                JOIN entity_relationships r2
                    ON r2.subject_uri IN (t.subject_uri, t.object_uri)
                    OR r2.object_uri IN (t.subject_uri, t.object_uri)
                WHERE t.depth < 2
            )
            SELECT DISTINCT ON (t.subject_uri, t.predicate, t.object_uri)
                t.subject_uri,
                s.entity_text AS subject_label,
                t.predicate,
                t.object_uri,
                o.entity_text AS object_label,
                t.depth
            FROM traverse t
            LEFT JOIN entity_registry s ON s.fuseki_uri = t.subject_uri
            LEFT JOIN entity_registry o ON o.fuseki_uri = t.object_uri
            WHERE NOT COALESCE(s.node_private, false)
              AND NOT COALESCE(o.node_private, false)
            ORDER BY t.subject_uri, t.predicate, t.object_uri, t.depth
            LIMIT 50
        """, entity_uris)
        for rr in rel_rows:
            subj = rr['subject_label'] or rr['subject_uri']
            obj = rr['object_label'] or rr['object_uri']
            relationships_ctx.append(f"{subj} --[{rr['predicate']}]--> {obj}")

    # ── Step 6: Document chunks (same as B1) ──
    try:
        chunk_rows = await conn.fetch("""
            SELECT
                c.document_rid,
                m.content->>'title' AS title,
                LEFT(c.content->>'text', 500) AS chunk_text,
                c.content->>'section_id' AS section_id,
                c.content->>'section_title' AS section_title,
                c.content->>'wiki_url' AS wiki_url,
                1 - (c.embedding <=> $1::vector) AS similarity
            FROM koi_memory_chunks c
            JOIN koi_memories m ON m.rid = c.document_rid
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> $1::vector
            LIMIT 8
        """, embedding_str)
        for cr in chunk_rows:
            if float(cr['similarity']) > 0.3:
                doc_chunks.append({
                    "rid": cr['document_rid'],
                    "title": cr['title'] or cr['document_rid'],
                    "text": cr['chunk_text'] or "",
                    "score": round(float(cr['similarity']), 4),
                    "section_id": cr['section_id'],
                    "section_title": cr['section_title'],
                    "wiki_url": cr['wiki_url'],
                })
                sources.append({
                    "uri": cr['document_rid'],
                    "label": cr['title'] or cr['document_rid'],
                    "entity_type": "Document",
                    "score": round(float(cr['similarity']), 4),
                    "description": (cr['chunk_text'] or "")[:200],
                    "url": cr['wiki_url'],
                })
    except Exception:
        pass

    # ── Step 7: Web sources (same as B1) ──
    if entity_uris:
        try:
            ws_rows = await conn.fetch("""
                SELECT DISTINCT ON (ws.url) ws.url, ws.title, ws.description
                FROM web_submissions ws
                JOIN document_entity_links del ON del.document_rid = 'web:' || ws.rid::text
                WHERE del.entity_uri = ANY($1) AND ws.status IN ('ingested', 'monitoring')
                LIMIT 5
            """, entity_uris)
            for wr in ws_rows:
                desc = wr['description'] or ""
                web_sources.append({"url": wr['url'], "title": wr['title'] or wr['url'], "summary": desc})
                sources.append({
                    "uri": wr['url'],
                    "label": wr['title'] or wr['url'],
                    "entity_type": "WebSource",
                    "score": 0.8,
                    "description": desc[:200],
                })
        except Exception:
            pass

    return sources, relationships_ctx, doc_chunks, web_sources


class ChatRequest(BaseModel):
    """Request for RAG chat."""
    query: str
    max_context_entities: int = Field(default=5, ge=1, le=20)
    retrieval_mode: str = Field(default="hybrid", description="hybrid (B1 default) or graphrag (B2 experimental)")


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """
    RAG chat: semantic-search the knowledge graph, build context from matched
    entities (labels, types, descriptions, relationships), then call an LLM to
    generate a grounded answer.

    Returns ``{ answer, sources, intent }`` matching the web-dashboard
    ChatResponse contract.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    # Lazy init openai_client for /chat LLM calls (separate from embedding provider)
    global openai_client
    if not openai_client:
        if not OPENAI_API_KEY:
            raise HTTPException(
                status_code=503,
                detail="LLM service not available (OPENAI_API_KEY not configured)",
            )
        try:
            from openai import OpenAI
            openai_client = OpenAI(api_key=OPENAI_API_KEY)
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail="openai package not installed",
            )

    # ------------------------------------------------------------------
    # 1. Semantic search over entity embeddings to find relevant entities
    # ------------------------------------------------------------------
    query_embedding = await generate_embedding(request.query)

    # ── B2 GraphRAG dispatch ──
    _use_graphrag = request.retrieval_mode == "graphrag"
    _graphrag_done = False  # True when graphrag produced usable results

    if _use_graphrag:
        async with db_pool.acquire() as conn:
            _gr_sources, _gr_rels, _gr_docs, _gr_web = await _graph_guided_retrieval(
                request.query, query_embedding, conn, top_k=request.max_context_entities
            )
        # Only use graphrag results if it actually found sources;
        # otherwise fall through to B1 so we don't serve empty context.
        if _gr_sources:
            sources = _gr_sources
            relationships_ctx = _gr_rels
            doc_chunks = _gr_docs
            web_sources = _gr_web
            _graphrag_done = True

    # ── B1 hybrid retrieval (skipped when graphrag produced results) ──
    if not _graphrag_done:
        sources: List[Dict[str, Any]] = []

    if not _graphrag_done:
      async with db_pool.acquire() as conn:
        if query_embedding:
            embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'
            try:
                rows = await conn.fetch("""
                    SELECT fuseki_uri, entity_text, entity_type, metadata,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM entity_registry
                    WHERE embedding IS NOT NULL AND NOT node_private
                    ORDER BY embedding <=> $1::vector
                    LIMIT $2
                """, embedding_str, request.max_context_entities)
            except (asyncpg.exceptions.UndefinedColumnError,
                    asyncpg.exceptions.UndefinedFunctionError,
                    asyncpg.exceptions.DataError) as e:
                logger.warning("entity_registry.embedding vector search failed, falling back to text search: %s", e)
                # Extract meaningful words (3+ chars) from query for keyword matching
                words = [w for w in request.query.lower().split() if len(w) >= 3]
                if words:
                    conditions = " OR ".join(f"normalized_text ILIKE ${i+1}" for i in range(len(words)))
                    match_score = " + ".join(
                        f"CASE WHEN normalized_text ILIKE ${i+1} THEN 1 ELSE 0 END"
                        for i in range(len(words))
                    )
                    params = [f"%{w}%" for w in words]
                    params.append(request.max_context_entities)
                    rows = await conn.fetch(f"""
                        SELECT fuseki_uri, entity_text, entity_type, metadata,
                               ({match_score})::float / {len(words)} AS similarity
                        FROM entity_registry
                        WHERE ({conditions}) AND NOT node_private
                        ORDER BY ({match_score}) DESC, created_at DESC
                        LIMIT ${len(words)+1}
                    """, *params)
                else:
                    rows = []
        else:
            # Fallback: text search on entity names using keyword splitting
            words = [w for w in request.query.lower().split() if len(w) >= 3]
            if words:
                conditions = " OR ".join(f"normalized_text ILIKE ${i+1}" for i in range(len(words)))
                match_score = " + ".join(
                    f"CASE WHEN normalized_text ILIKE ${i+1} THEN 1 ELSE 0 END"
                    for i in range(len(words))
                )
                params = [f"%{w}%" for w in words]
                params.append(request.max_context_entities)
                rows = await conn.fetch(f"""
                    SELECT fuseki_uri, entity_text, entity_type, metadata,
                           ({match_score})::float / {len(words)} AS similarity
                    FROM entity_registry
                    WHERE ({conditions}) AND NOT node_private
                    ORDER BY ({match_score}) DESC, created_at DESC
                    LIMIT ${len(words)+1}
                """, *params)
            else:
                rows = []

        # Build source list and collect URIs for relationship lookup
        entity_uris = []
        for row in rows:
            meta = row['metadata'] or {}
            if isinstance(meta, str):
                meta = json_module_global.loads(meta)
            description = meta.get('description', '') if isinstance(meta, dict) else ''
            sources.append({
                "uri": row['fuseki_uri'],
                "label": row['entity_text'],
                "entity_type": row['entity_type'],
                "score": round(float(row['similarity']), 4),
                "description": description,
                "quartz_url": quartz_url(row['entity_type'], row['entity_text']),
            })
            entity_uris.append(row['fuseki_uri'])

        # ------------------------------------------------------------------
        # 2. Fetch 2-hop relationships for matched entities (richer context)
        # ------------------------------------------------------------------
        relationships_ctx: List[str] = []
        if entity_uris:
            rel_rows = await conn.fetch("""
                WITH RECURSIVE traverse AS (
                    SELECT
                        r.subject_uri, r.object_uri, r.predicate,
                        1 AS depth
                    FROM entity_relationships r
                    WHERE r.subject_uri = ANY($1) OR r.object_uri = ANY($1)
                    UNION
                    SELECT
                        r2.subject_uri, r2.object_uri, r2.predicate,
                        t.depth + 1
                    FROM traverse t
                    JOIN entity_relationships r2
                        ON r2.subject_uri IN (t.subject_uri, t.object_uri)
                        OR r2.object_uri IN (t.subject_uri, t.object_uri)
                    WHERE t.depth < 2
                )
                SELECT DISTINCT ON (t.subject_uri, t.predicate, t.object_uri)
                    t.subject_uri,
                    s.entity_text AS subject_label,
                    t.predicate,
                    t.object_uri,
                    o.entity_text AS object_label,
                    t.depth
                FROM traverse t
                LEFT JOIN entity_registry s ON s.fuseki_uri = t.subject_uri
                LEFT JOIN entity_registry o ON o.fuseki_uri = t.object_uri
                WHERE NOT COALESCE(s.node_private, false)
                  AND NOT COALESCE(o.node_private, false)
                ORDER BY t.subject_uri, t.predicate, t.object_uri, t.depth
                LIMIT 50
            """, entity_uris)
            for rr in rel_rows:
                subj = rr['subject_label'] or rr['subject_uri']
                obj = rr['object_label'] or rr['object_uri']
                relationships_ctx.append(f"{subj} --[{rr['predicate']}]--> {obj}")

        # ------------------------------------------------------------------
        # 2b. Search document chunks for grounding text (B1.1)
        # ------------------------------------------------------------------
        doc_chunks: List[Dict[str, Any]] = []
        if query_embedding:
            try:
                chunk_rows = await conn.fetch("""
                    SELECT
                        c.document_rid,
                        m.content->>'title' AS title,
                        LEFT(c.content->>'text', 500) AS chunk_text,
                        c.content->>'section_id' AS section_id,
                        c.content->>'section_title' AS section_title,
                        c.content->>'wiki_url' AS wiki_url,
                        1 - (c.embedding <=> $1::vector) AS similarity
                    FROM koi_memory_chunks c
                    JOIN koi_memories m ON m.rid = c.document_rid
                    WHERE c.embedding IS NOT NULL
                    ORDER BY c.embedding <=> $1::vector
                    LIMIT 8
                """, embedding_str)
                for cr in chunk_rows:
                    if float(cr['similarity']) > 0.3:
                        doc_chunks.append({
                            "rid": cr['document_rid'],
                            "title": cr['title'] or cr['document_rid'],
                            "text": cr['chunk_text'] or "",
                            "score": round(float(cr['similarity']), 4),
                            "section_id": cr['section_id'],
                            "section_title": cr['section_title'],
                            "wiki_url": cr['wiki_url'],
                        })
                        sources.append({
                            "uri": cr['document_rid'],
                            "label": cr['title'] or cr['document_rid'],
                            "entity_type": "Document",
                            "score": round(float(cr['similarity']), 4),
                            "description": (cr['chunk_text'] or "")[:200],
                            "url": cr['wiki_url'],
                        })
            except (asyncpg.exceptions.UndefinedTableError,
                    asyncpg.exceptions.UndefinedColumnError):
                pass  # koi_memory_chunks or expected columns not available
            except asyncpg.exceptions.DataError as e:
                # Dimension mismatch: koi_memory_chunks uses BGE embeddings (1024-dim)
                # while generate_embedding() may output a different dimension (e.g. 1536).
                logger.warning(f"koi_memory_chunks vector dimension mismatch (BGE vs provider): {e}")
                pass  # skip chunk results, /chat still works with entity context

        # ------------------------------------------------------------------
        # 2c. Fetch web sources linked to matched entities (B1.3)
        # ------------------------------------------------------------------
        web_sources: List[Dict[str, Any]] = []
        if entity_uris:
            try:
                ws_rows = await conn.fetch("""
                    SELECT DISTINCT ON (ws.url)
                        ws.url,
                        ws.title,
                        ws.description
                    FROM web_submissions ws
                    JOIN document_entity_links del
                        ON del.document_rid = 'web:' || ws.rid::text
                    WHERE del.entity_uri = ANY($1)
                      AND ws.status IN ('ingested', 'monitoring')
                    LIMIT 5
                """, entity_uris)
                for wr in ws_rows:
                    desc = wr['description'] or ""
                    web_sources.append({
                        "url": wr['url'],
                        "title": wr['title'] or wr['url'],
                        "summary": desc,
                    })
                    sources.append({
                        "uri": wr['url'],
                        "label": wr['title'] or wr['url'],
                        "entity_type": "WebSource",
                        "score": 0.8,
                        "description": desc[:200],
                    })
            except (asyncpg.exceptions.UndefinedTableError,
                    asyncpg.exceptions.UndefinedColumnError):
                pass  # web_submissions or expected columns not available

    # ------------------------------------------------------------------
    # 3. Build LLM prompt with entity context
    # ------------------------------------------------------------------
    entity_block = "\n".join(
        f"- {s['label']} ({s['entity_type']})"
        + (f": {s['description']}" if s.get('description') else "")
        for s in sources
        if s['entity_type'] not in ('Document', 'WebSource')
    ) or "(no matching entities found)"

    rel_block = "\n".join(f"- {r}" for r in relationships_ctx) or "(none)"

    doc_block = "\n".join(
        f"- **{d['title']}**"
        + (f" (Section: {d['section_title']})" if d.get('section_title') else "")
        + (f" [source]({d['wiki_url']})" if d.get('wiki_url') else "")
        + f": {d['text'][:300]}"
        for d in doc_chunks
    ) if doc_chunks else ""

    web_block = "\n".join(
        f"- [{w['title']}]({w['url']}): {w['summary'][:200]}"
        for w in web_sources
    ) if web_sources else ""

    system_prompt = (
        "You are a knowledgeable assistant for a bioregional knowledge commons "
        "focused on ecological stewardship, regenerative practices, and community "
        "governance in bioregions. Answer the user's question using the entity, "
        "relationship, document, and web source context provided below. "
        "Cite specific entities and sources in your answer. "
        "When referencing wiki sources, cite them as [Page > Section](url). "
        "If the context is insufficient, say so honestly. Be concise."
    )

    prompt_sections = [f"## Relevant Entities\n{entity_block}"]
    prompt_sections.append(f"## Relationships\n{rel_block}")
    if doc_block:
        prompt_sections.append(f"## Relevant Documents\n{doc_block}")
    if web_block:
        prompt_sections.append(f"## Web Sources\n{web_block}")
    prompt_sections.append(f"## Question\n{request.query}")
    user_prompt = "\n\n".join(prompt_sections)

    # ------------------------------------------------------------------
    # 4. Call LLM
    # ------------------------------------------------------------------
    try:
        llm_response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model=CHAT_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        answer = llm_response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"LLM call failed: {e}",
        )

    # ------------------------------------------------------------------
    # 5. Build intent object (lightweight classification)
    # ------------------------------------------------------------------
    entity_labels = [s['label'] for s in sources]
    intent = {
        "intent": "knowledge_query",
        "entities": entity_labels,
        "confidence": round(max((s['score'] for s in sources), default=0.0), 4),
    }

    return {
        "answer": answer,
        "sources": sources,
        "intent": intent,
        "retrieval_mode": request.retrieval_mode,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('KOI_API_PORT', '8351'))
    uvicorn.run(app, host="0.0.0.0", port=port)
