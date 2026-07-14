"""Pure entity resolution primitives.

No module-level state. No external API calls.
Embedding generation is injected as an optional async callable.

Copied from personal_ingest_api.py for use in the KOI-net pipeline
without pulling in the full app module and its side effects.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from api.entity_schema import get_schema_for_type

logger = logging.getLogger(__name__)

# Token overlap constants (matches personal_ingest_api.py)
MIN_TOKEN_OVERLAP_RATIO = 0.5
MIN_TOKEN_OVERLAP_COUNT = 2


def normalize_entity_text(text: str) -> str:
    """Normalize entity text for comparison."""
    return (
        text.lower()
        .strip()
        .replace("_", " ")
        .replace("-", " ")
        .replace("  ", " ")
        .lstrip("@")
    )


def normalize_alias(alias: Any) -> str:
    """Strip [[...]], lowercase, trim for alias matching.

    Handles wikilinks like [[People/Name|Display]] -> name
    """
    alias = str(alias)
    alias = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", alias)
    if "/" in alias:
        alias = alias.rsplit("/", 1)[-1]
    alias = alias.lower().strip()
    return alias


def normalize_alias_list(values: Any) -> list:
    """Normalize a list of alias strings to the canonical matchable form.

    Applies normalize_alias() to each value, drops empties, and dedupes while
    preserving first-seen order. Use this at EVERY alias-write path so stored
    aliases stay in the same form the resolver queries with
    (WHERE $2 = ANY(aliases), $2 = normalize_alias(input)). See plan
    alias-normalization-fix.
    """
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list = []
    seen = set()
    for v in values:
        if v is None:
            continue  # skip before normalize_alias (str(None) -> "none")
        n = normalize_alias(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def jaro_winkler_similarity(s1: str, s2: str) -> float:
    """Calculate Jaro-Winkler similarity between two strings."""
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
        matches / len1
        + matches / len2
        + (matches - transpositions / 2) / matches
    ) / 3

    prefix_len = 0
    for i in range(min(4, min(len1, len2))):
        if s1[i] == s2[i]:
            prefix_len += 1
        else:
            break

    return jaro + prefix_len * 0.1 * (1 - jaro)


def compute_token_overlap(text1: str, text2: str) -> Tuple[float, int]:
    """Compute token (word) overlap between two texts.

    Returns: (overlap_ratio, overlap_count)
    """
    tokens1 = set(text1.lower().split())
    tokens2 = set(text2.lower().split())
    overlap = tokens1 & tokens2
    overlap_count = len(overlap)
    shorter_len = min(len(tokens1), len(tokens2))
    if shorter_len == 0:
        return 0.0, 0
    return overlap_count / shorter_len, overlap_count


def passes_token_overlap_check(text1: str, text2: str, entity_type: str) -> bool:
    """Check if two texts pass the token overlap requirement."""
    schema = get_schema_for_type(entity_type)
    if not schema.require_token_overlap:
        return True

    overlap_ratio, overlap_count = compute_token_overlap(text1, text2)
    tokens1 = text1.lower().split()
    tokens2 = text2.lower().split()
    if len(tokens1) == 1 or len(tokens2) == 1:
        return True
    if overlap_ratio < MIN_TOKEN_OVERLAP_RATIO:
        return False
    if overlap_count < MIN_TOKEN_OVERLAP_COUNT:
        return False
    return True


# =============================================================================
# Shared resolver name guards (P1 — 2026-07-13)
#
# These are the single source of truth for the person-name, distinctive-token
# and semantic-match guards. `personal_ingest_api.py` imports them (no
# duplication) and wires them into its Tier 1.5 / 2a / 2b, and
# `resolve_entity_multi_tier` below applies them in its Tier 2a / 2b.
# =============================================================================

# Generic "scaffolding" tokens that carry little identity signal for
# Organization / Project / Concept names. Two names that share ONLY generic
# tokens (e.g. "University of Guelph" vs "University of Melbourne") are not the
# same entity.
GENERIC_NAME_TOKENS = {
    "of", "the", "and", "for", "a", "in",
    "institute", "university", "college", "foundation", "network",
    "initiative", "project", "program", "alliance", "association",
    "council", "group", "lab", "labs", "camp", "team", "fund", "trust",
    "society", "collective", "coalition", "centre", "center",
}


def passes_person_name_guard(query_norm: str, cand_norm: str) -> bool:
    """Guard Person merges against first-name / single-token collisions.

    Applied wherever a Person candidate would be accepted by fuzzy, semantic,
    or contextual similarity. Registered aliases are resolved earlier
    (Tier 1.1), so this guard can be strict:

    - Multi-token vs single-token (either direction) -> False. A bare first
      name ("Dan", "Carol", "Anthony") must not collapse into a full name
      unless a registered alias says so ("Dan" -> Dana Tizya-Tramm is handled
      by Tier 1.1, not here).
    - Both multi-token -> require Jaro-Winkler >= 0.85 on the FIRST tokens AND
      >= 0.85 on the LAST tokens, independently. Rejects "Carol Newell" vs
      "Carol Anne" (last JW 0.61), "Kevin Owocki" vs "Kevin Triplett"
      (last 0.43), "Sarah Wilshaw" vs "Sarah Wilson" (last 0.848) while
      allowing spelling variants of the same full name.
    - Single vs single -> True; the caller's single-word JW>=0.95 rule
      (passes_token_overlap_check) is the gate for those.

    Inputs are expected to be normalized (lowercased, hyphen/underscore ->
    space) but the function lowercases defensively.
    """
    q = query_norm.lower().split()
    c = cand_norm.lower().split()
    if not q or not c:
        return False
    q_multi = len(q) >= 2
    c_multi = len(c) >= 2
    if q_multi != c_multi:
        return False  # multi vs single — never merge without a registered alias
    if not q_multi:
        return True   # single vs single — deferred to single-word JW rule
    first_jw = jaro_winkler_similarity(q[0], c[0])
    last_jw = jaro_winkler_similarity(q[-1], c[-1])
    return first_jw >= 0.85 and last_jw >= 0.85


def _distinctive_tokens(text: str) -> set:
    """Tokens of `text` with generic scaffolding words removed."""
    return {t for t in text.lower().split() if t not in GENERIC_NAME_TOKENS}


def passes_distinctive_token_check(text1: str, text2: str) -> bool:
    """Distinctive-token guard for Organization / Project / Concept merges.

    Strips generic tokens (of / the / institute / university / network / ...)
    and reasons about the remaining *distinctive* tokens:

    - Both sides have >=1 distinctive token and the distinctive sets are
      DISJOINT -> reject. E.g. "University of Guelph" vs "University of
      Melbourne" -> {guelph} vs {melbourne}.
    - One side's distinctive set is a STRICT SUPERSET of the other's (a
      token-level extension, e.g. {dweb, cascadia} over {dweb}) -> reject
      unless the full strings are near-identical (JW >= 0.97).

    Otherwise pass (defer to the caller's token-overlap-count / JW rules).
    Returns True when either side has no distinctive tokens (nothing to
    compare) so purely-generic names fall through to other checks.
    """
    d1 = _distinctive_tokens(text1)
    d2 = _distinctive_tokens(text2)
    if not d1 or not d2:
        return True
    if d1.isdisjoint(d2):
        return False
    if d1 < d2 or d2 < d1:  # strict superset in either direction
        if jaro_winkler_similarity(text1.lower(), text2.lower()) < 0.97:
            return False
    return True


def passes_semantic_match_guard(
    entity_type: str,
    query_norm: str,
    match_text_norm: str,
    similarity: float,
    threshold: float,
) -> bool:
    """Composite guard applied to a Tier 2b (semantic) candidate before accept.

    Semantic similarity alone conflates same-first-name people, short generic
    names, and disjoint multi-word names. This layers the name-shape guards on
    top:

    (a) Person -> passes_person_name_guard.
    (b) Any multi-token pair -> passes_token_overlap_check (must share tokens),
        plus the distinctive-token guard for Organization/Project/Concept.
    (c) Short names (query <=2 tokens or <12 chars) -> require the embedding
        similarity to clear threshold by a 0.03 margin (short names embed
        weakly, so demand a stronger signal).
    """
    if entity_type == "Person" and not passes_person_name_guard(query_norm, match_text_norm):
        return False

    q = query_norm.split()
    m = match_text_norm.split()
    if len(q) >= 2 and len(m) >= 2:
        if not passes_token_overlap_check(query_norm, match_text_norm, entity_type):
            return False
    if entity_type in ("Organization", "Project", "Concept"):
        if not passes_distinctive_token_check(query_norm, match_text_norm):
            return False

    if len(q) <= 2 or len(query_norm) < 12:
        if similarity < threshold + 0.03:
            return False
    return True


async def resolve_entity_multi_tier(
    conn,
    entity_name: str,
    entity_type: str,
    mode: str = "exact_alias",
    embed_fn: Optional[Callable[[str], Awaitable[Optional[List[float]]]]] = None,
) -> Tuple[Optional[str], float, str]:
    """Multi-tier entity resolution against entity_registry.

    Tiers enabled by mode:
    - "exact": Tier 1 only
    - "exact_alias": Tiers 1 + 1.1
    - "fuzzy": Tiers 1 + 1.1 + 2a
    - "semantic": Tiers 1 + 1.1 + 2a + 2b (requires embed_fn)

    Does NOT include Tier 1.5 (contextual) or Tier 3 (create new entity).
    Returns: (local_uri or None, confidence, relationship)
    """
    normalized = normalize_entity_text(entity_name)

    # --- Tier 1: Exact match on normalized_text ---
    row = await conn.fetchrow(
        """
        SELECT fuseki_uri FROM entity_registry
        WHERE normalized_text = $1 AND entity_type = $2
        """,
        normalized,
        entity_type,
    )
    if row:
        return row["fuseki_uri"], 1.0, "same_as"

    if mode == "exact":
        return None, 0.0, "unresolved"

    # --- Tier 1.1: Alias match ---
    alias_norm = normalize_alias(entity_name)
    rows = await conn.fetch(
        """
        SELECT fuseki_uri, aliases FROM entity_registry
        WHERE entity_type = $1 AND aliases IS NOT NULL
        """,
        entity_type,
    )
    for r in rows:
        aliases = r["aliases"]
        if isinstance(aliases, str):
            import json as _json

            try:
                aliases = _json.loads(aliases)
            except (ValueError, TypeError):
                aliases = [aliases]
        if not isinstance(aliases, list):
            continue
        for a in aliases:
            if normalize_alias(a) == alias_norm:
                return r["fuseki_uri"], 1.0, "same_as"

    if mode == "exact_alias":
        return None, 0.0, "unresolved"

    # --- Tier 2a: Fuzzy (Jaro-Winkler) ---
    schema = get_schema_for_type(entity_type)
    threshold = schema.similarity_threshold

    candidates = await conn.fetch(
        """
        SELECT fuseki_uri, normalized_text FROM entity_registry
        WHERE entity_type = $1
        """,
        entity_type,
    )
    best_uri = None
    best_score = 0.0

    for c in candidates:
        cand_norm = c["normalized_text"]
        score = jaro_winkler_similarity(normalized, cand_norm)
        if score >= threshold and score > best_score:
            if not passes_token_overlap_check(normalized, cand_norm, entity_type):
                continue
            # Shared P1 guards (same as personal_ingest_api Tier 2a).
            if entity_type == "Person" and not passes_person_name_guard(normalized, cand_norm):
                logger.info(
                    "multi-tier Tier 2a REJECTED (person name guard): %r vs %r",
                    entity_name, cand_norm,
                )
                continue
            if entity_type in ("Organization", "Project", "Concept") and \
                    not passes_distinctive_token_check(normalized, cand_norm):
                logger.info(
                    "multi-tier Tier 2a REJECTED (distinctive token guard): %r vs %r",
                    entity_name, cand_norm,
                )
                continue
            best_score = score
            best_uri = c["fuseki_uri"]

    if best_uri:
        return best_uri, best_score, "related_to"

    if mode == "fuzzy":
        return None, 0.0, "unresolved"

    # --- Tier 2b: Semantic (embedding similarity) ---
    if not embed_fn:
        return None, 0.0, "unresolved"

    query_embedding = await embed_fn(normalized)
    if not query_embedding:
        return None, 0.0, "unresolved"

    semantic_threshold = schema.semantic_threshold

    # pgvector cosine distance: 1 - distance = similarity.
    # Reads from embedding_3072 (post-2026-04-23 OpenAI 3072-dim migration);
    # halfvec cast required because pgvector full-precision vector ANN caps
    # at 2000 dims. Uses idx_entity_registry_embedding_3072_hnsw.
    sem_row = await conn.fetchrow(
        """
        SELECT fuseki_uri, normalized_text,
               1 - (embedding_3072::halfvec(3072)
                    <=> $1::halfvec(3072)) AS similarity
        FROM entity_registry
        WHERE entity_type = $2 AND embedding_3072 IS NOT NULL
        ORDER BY embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
        LIMIT 1
        """,
        str(query_embedding),
        entity_type,
    )
    if sem_row and sem_row["similarity"] >= semantic_threshold:
        # Semantic candidate must clear the shared name-shape guards before we
        # accept it (matches personal_ingest_api Tier 2b).
        sem_norm = sem_row["normalized_text"] or ""
        if sem_norm and not passes_semantic_match_guard(
            entity_type, normalized, sem_norm,
            float(sem_row["similarity"]), semantic_threshold,
        ):
            logger.info(
                "multi-tier Tier 2b REJECTED (name guard): %r vs %r (sim=%.3f)",
                entity_name, sem_norm, float(sem_row["similarity"]),
            )
            return None, 0.0, "unresolved"
        return sem_row["fuseki_uri"], float(sem_row["similarity"]), "related_to"

    return None, 0.0, "unresolved"
