# Agent Task: FIX-004 - Role/Group Detection Upgrade

## Context

FIX-001 (namespace), FIX-002 (extractor unification), and FIX-003 (ENTITY-default fix + pipeline ordering) are DEPLOYED. FIX-004 is part of P2 (Improve Filters) and should land before Stage 6 re-extraction.

**Problem:** Role/group terms are being extracted as PERSON due to gaps in term coverage:
1. `GENERIC_GROUP_TERMS` only contains plural forms (e.g., "buyers") but misses singular (e.g., "buyer")
2. Multi-word role patterns aren't caught (e.g., "Development Team", "Partnerships Lead")
3. Cosmos SDK terms like "Keeper" are typed as PERSON instead of being blocked or retyped

**Evidence:**
- ≥115 role/group-like PERSON entities in entity_registry
- Error IDs: E057-E071

## Objective

Implement FIX-004 to:
1. Expand `GENERIC_GROUP_TERMS` with singular forms
2. Add regex-based role pattern detection
3. Handle Cosmos SDK terms (block as PERSON for now)
4. Apply "drop vs retag" policy for role terms (default: DROP as PERSON; retagging is optional and NOT required for FIX-004)

## Guardrails

- Implement code + tests only. Do NOT run full re-extraction as part of FIX-004.
- Do NOT run against production DB/Fuseki unless explicitly instructed.
- Do NOT run data migration SQL (Stage 6 re-extraction will clean data).

## Repo Paths

- Server: /opt/projects/koi-processor (production)
- Local: /Users/darrenzal/projects/RegenAI/koi-processor
- Documentation: /Users/darrenzal/projects/RegenAI/knowledge-graph-review-2025-12.md

## Read First (required)

| File | Purpose |
|------|---------|
| `src/knowledge_graph/improvements/entity_quality_filter.py` | Search for `GENERIC_GROUP_TERMS`, `is_generic_group`, and where `generic_group` is appended in `filter_with_reasons()` |
| `src/extraction/prompt_builder.py` | FIX-002 shared prompt builder (may need PERSON vs GROUP guidance) |

## Implementation Steps

### Step 1: Expand GENERIC_GROUP_TERMS with Singular Forms

In `entity_quality_filter.py`, update the `GENERIC_GROUP_TERMS` set to include both singular and plural forms:

```python
# Replace existing GENERIC_GROUP_TERMS (around line 419-430)
GENERIC_GROUP_TERMS: Set[str] = {
    # Economic actors (singular + plural)
    "buyer", "buyers", "seller", "sellers", "trader", "traders",
    "investor", "investors", "stakeholder", "stakeholders",
    "partner", "partners", "sponsor", "sponsors",
    "funder", "funders", "donor", "donors", "backer", "backers",

    # Organizational roles (singular + plural)
    "user", "users", "member", "members", "participant", "participants",
    "contributor", "contributors", "volunteer", "volunteers",
    "admin", "admins", "administrator", "administrators",
    "moderator", "moderators", "coordinator", "coordinators",
    "validator", "validators", "delegator", "delegators",
    "voter", "voters", "creator", "creators",

    # Teams/groups (singular + plural)
    "team", "teams", "group", "groups", "community", "communities",
    "committee", "committees", "council", "councils",
    "network", "networks", "coalition", "coalitions",

    # Service providers (singular + plural)
    "developer", "developers", "builder", "builders",
    "operator", "operators", "provider", "providers",
    "auditor", "auditors", "verifier", "verifiers",

    # Cosmos SDK / blockchain terms (block as PERSON)
    "keeper", "keepers", "relayer", "relayers",
    "proposer", "proposers", "depositor", "depositors",
}
```

### Step 2: Add Regex-Based Role Pattern Detection

Add new class attributes and method for pattern-based detection:

```python
# Add to EntityQualityFilter class (after GENERIC_GROUP_TERMS)

# Multi-word role patterns that should not be PERSON
ROLE_PATTERNS: List[re.Pattern] = [
    # Department + title patterns (avoid blocking real surnames like "Michael Head")
    # Examples: "Partnerships Lead", "Comms Lead", "Governance Director", "Engineering Manager"
    re.compile(
        r'^(partnerships?|comms?|communications?|governance|dev|development|engineering|product|project|program|operations|ops|marketing|growth|community|core|research|design|finance|legal|security|data)\s+'
        r'(lead|manager|director|head|chief|officer)$',
        re.IGNORECASE,
    ),
    # Named role collectives
    re.compile(r'^(core|community)\s+contributors?$', re.IGNORECASE),
    # Team/group qualifiers
    re.compile(r'\b(team|group|committee|council|task\s*force|working\s*group)\b', re.IGNORECASE),
    # Generic actor patterns
    re.compile(r'\b(buyers?|sellers?|traders?|investors?)\b', re.IGNORECASE),
    re.compile(r'\b(validators?|delegators?|voters?|proposers?)\b', re.IGNORECASE),
    re.compile(r'\b(developers?|builders?|contributors?|operators?|auditors?|verifiers?)\b', re.IGNORECASE),
    # Cosmos SDK module roles
    re.compile(r'\b(keepers?|relayers?|depositors?)\b', re.IGNORECASE),
]

def matches_role_pattern(self, name: str, entity_type: str) -> bool:
    """
    Check if name matches multi-word role patterns.

    Only applies to PERSON/HUMANACTOR/ENTITY types.
    Catches: "Development Team", "Partnerships Lead", "Carbon Credit Buyers"
    """
    if not entity_type or entity_type.upper() not in ('PERSON', 'HUMANACTOR', 'ENTITY'):
        return False

    stripped = name.strip()
    if len(stripped.split()) < 2:
        return False  # patterns are for multi-word roles only

    for pattern in self.ROLE_PATTERNS:
        if pattern.search(stripped):
            return True
    return False
```

### Step 3: Update is_generic_group() to Use Pattern Matching

Enhance the existing method to also call `matches_role_pattern()`:

```python
def is_generic_group(self, name: str, entity_type: str) -> bool:
    """
    Check for generic group/role terms that should not be PERSON entities.

    Blocks: "Buyers", "Partners", "Development Team", "Partnerships Lead"
    Allows: Proper person names.

    FIX-004: Now includes regex patterns for multi-word roles.
    """
    if not entity_type or entity_type.upper() not in ('PERSON', 'HUMANACTOR', 'ENTITY'):
        return False

    normalized = name.strip().lower()

    # 1. Standalone terms (e.g., "buyers", "buyer", "partners")
    if normalized in self.GENERIC_GROUP_TERMS:
        return True

    # 2. Compound terms: check last token ("water utilities", "carbon credit buyers")
    parts = normalized.split()
    if len(parts) >= 2:
        last = parts[-1]
        if last in self.GENERIC_GROUP_TERMS:
            return True

    # 3. FIX-004: Regex patterns for multi-word roles
    if self.matches_role_pattern(name, entity_type):
        return True

    return False
```

**Note:** Remove the old singular-form check (`normalized.endswith("s") and normalized[:-1] in GENERIC_GROUP_TERMS`) since we now include both forms explicitly in the set.

### Step 4: Add Retag-to-ORGANIZATION Option (Optional Enhancement)

For named teams/groups, consider retagging instead of dropping. Add a helper method:

```python
# Named group patterns that could be retagged to ORGANIZATION
NAMED_GROUP_INDICATORS = [
    re.compile(r'^(the\s+)?[A-Z][a-z]+(\s+[A-Z][a-z]+)*\s+(Team|Group|Committee|Council|Community)$'),
    # e.g., "Regen Network Team", "The Governance Council"
]

def should_retag_to_organization(self, name: str, entity_type: str) -> bool:
    """
    Check if a PERSON entity is actually a named group that should be ORGANIZATION.

    Only retag if name appears to be a proper noun + group term.
    """
    if not entity_type or entity_type.upper() not in ('PERSON', 'HUMANACTOR'):
        return False

    for pattern in self.NAMED_GROUP_INDICATORS:
        if pattern.match(name.strip()):
            return True
    return False
```

**For now:** Focus on blocking (dropping) role terms. Retagging can be a follow-up enhancement if needed.

### Step 5: Update Filter Flow to Use New Methods

Ensure the enhanced `is_generic_group()` is properly integrated. The existing code at lines 962-963 already calls it:

```python
# 5.5 Generic group terms extracted as PERSON
if self.is_generic_group(name, entity_type):
    reasons.append("generic_group")
```

No changes needed here - the method signature is unchanged.

## Testing Plan

### Unit Tests

Create `tests/test_fix004_role_detection.py`:

```python
import pytest
from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter


class TestFix004RoleDetection:
    """FIX-004: Role/Group detection tests."""

    @pytest.fixture
    def filter(self):
        return EntityQualityFilter()

    # ========================================================================
    # Test singular/plural forms
    # ========================================================================

    def test_blocks_singular_role_terms(self, filter):
        """Singular forms should be blocked."""
        assert filter.is_generic_group("buyer", "PERSON") == True
        assert filter.is_generic_group("validator", "PERSON") == True
        assert filter.is_generic_group("developer", "PERSON") == True
        assert filter.is_generic_group("team", "PERSON") == True
        assert filter.is_generic_group("keeper", "PERSON") == True

    def test_blocks_plural_role_terms(self, filter):
        """Plural forms should be blocked."""
        assert filter.is_generic_group("buyers", "PERSON") == True
        assert filter.is_generic_group("validators", "PERSON") == True
        assert filter.is_generic_group("developers", "PERSON") == True
        assert filter.is_generic_group("teams", "PERSON") == True
        assert filter.is_generic_group("keepers", "PERSON") == True

    # ========================================================================
    # Test multi-word patterns
    # ========================================================================

    def test_blocks_compound_role_terms(self, filter):
        """Compound terms ending in role word should be blocked."""
        assert filter.is_generic_group("carbon credit buyers", "PERSON") == True
        assert filter.is_generic_group("water utilities", "PERSON") == True
        assert filter.is_generic_group("network validators", "PERSON") == True

    def test_blocks_role_patterns(self, filter):
        """Multi-word role patterns should be blocked."""
        assert filter.is_generic_group("Development Team", "PERSON") == True
        assert filter.is_generic_group("Partnerships Lead", "PERSON") == True
        assert filter.is_generic_group("Comms Lead", "PERSON") == True
        assert filter.is_generic_group("Governance Committee", "PERSON") == True
        assert filter.is_generic_group("Core Contributors", "PERSON") == True

    # ========================================================================
    # Test proper names are NOT blocked
    # ========================================================================

    def test_allows_proper_person_names(self, filter):
        """Proper person names should NOT be blocked."""
        assert filter.is_generic_group("Gregory Landua", "PERSON") == False
        assert filter.is_generic_group("Alice Johnson", "PERSON") == False
        assert filter.is_generic_group("Satoshi Nakamoto", "PERSON") == False
        assert filter.is_generic_group("Will Szal", "PERSON") == False
        assert filter.is_generic_group("Michael Head", "PERSON") == False  # surname collision guard

    def test_allows_non_person_types(self, filter):
        """Role terms as other types should NOT be blocked."""
        assert filter.is_generic_group("validators", "TECHNOLOGY") == False
        assert filter.is_generic_group("Development Team", "ORGANIZATION") == False
        assert filter.is_generic_group("buyers", "CONCEPT") == False

    # ========================================================================
    # Test Cosmos SDK terms
    # ========================================================================

    def test_blocks_cosmos_sdk_terms(self, filter):
        """Cosmos SDK role terms should be blocked as PERSON."""
        assert filter.is_generic_group("Keeper", "PERSON") == True
        assert filter.is_generic_group("keepers", "PERSON") == True
        assert filter.is_generic_group("relayer", "PERSON") == True
        assert filter.is_generic_group("depositor", "PERSON") == True

    # ========================================================================
    # Test matches_role_pattern directly
    # ========================================================================

    def test_matches_role_pattern(self, filter):
        """Test regex pattern matching for roles."""
        assert filter.matches_role_pattern("Project Lead", "PERSON") == True
        assert filter.matches_role_pattern("Team Manager", "PERSON") == True
        assert filter.matches_role_pattern("Working Group", "PERSON") == True
        assert filter.matches_role_pattern("Task Force", "PERSON") == True

        # Proper names should not match
        assert filter.matches_role_pattern("Gregory Landua", "PERSON") == False
        assert filter.matches_role_pattern("Regen Network", "ORGANIZATION") == False
```

### Integration Test

```python
def test_filter_with_reasons_blocks_roles():
    """Full filter flow should block role terms."""
    filter = EntityQualityFilter()

    # Should be blocked with "generic_group" reason
    is_valid, reasons = filter.filter_with_reasons("Development Team", "PERSON")
    assert is_valid == False
    assert "generic_group" in reasons

    is_valid, reasons = filter.filter_with_reasons("validator", "PERSON")
    assert is_valid == False
    assert "generic_group" in reasons

    # Proper name should pass
    is_valid, reasons = filter.filter_with_reasons("Gregory Landua", "PERSON")
    assert is_valid == True
```

### Validation Queries (run after Stage 6 re-extraction)

```sql
-- Check remaining role terms as PERSON (should be 0 after re-extraction)
SELECT entity_text, COUNT(*) FROM entity_registry
WHERE entity_type = 'PERSON'
  AND LOWER(entity_text) ~* '\b(buyers?|sellers?|traders?|validators?|delegators?|developers?|teams?|groups?|keepers?)\b'
GROUP BY entity_text
ORDER BY COUNT(*) DESC;
-- Expected: 0 rows (or very few edge cases)

-- Check for multi-word role patterns still as PERSON
SELECT entity_text FROM entity_registry
WHERE entity_type = 'PERSON'
  AND LOWER(entity_text) ~* '\b(lead|manager|team|group|committee|council)\b';
-- Expected: 0 rows
```

## Success Criteria

- [ ] `GENERIC_GROUP_TERMS` includes both singular and plural forms
- [ ] `ROLE_PATTERNS` list covers multi-word role patterns (Lead, Manager, Team, Group, Committee, etc.)
- [ ] `matches_role_pattern()` method exists and works for PERSON/HUMANACTOR/ENTITY types
- [ ] `is_generic_group()` calls `matches_role_pattern()` for comprehensive detection
- [ ] Cosmos SDK terms (Keeper, Relayer, Depositor) are blocked as PERSON
- [ ] Proper person names (Gregory Landua, Alice Johnson) are NOT blocked
- [ ] All unit tests pass
- [ ] No regression in existing tests (run full test suite)

## Do NOT

- Do NOT run data migration SQL (Stage 6 re-extraction will clean data)
- Do NOT modify FIX-001/FIX-002/FIX-003 code (already deployed)
- Do NOT change database schema
- Do NOT push to production without running tests locally first
- Do NOT run full re-extraction as part of FIX-004

## After Completion

1. Run tests locally:
   ```bash
   cd /Users/darrenzal/projects/RegenAI/koi-processor
   PYTHONPATH=src pytest tests/test_fix004_role_detection.py -v
   # Targeted KG regression suite (avoid collecting legacy/optional tests)
   PYTHONPATH=src pytest -q \
     tests/test_fix002_extractor_contract.py \
     tests/test_pipeline_modules.py \
     tests/test_fix003_entity_validation.py \
     tests/test_fix004_role_detection.py
   ```
2. Sync to production server:
   ```bash
   scp src/knowledge_graph/improvements/entity_quality_filter.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/improvements/
   scp tests/test_fix004_role_detection.py darren@202.61.196.119:/opt/projects/koi-processor/tests/
   ```
3. Run tests on production (only if asked to deploy)
4. Update status table in `knowledge-graph-review-2025-12.md`:
   ```
   | FIX-004 | DEPLOYED | ...koi-processor | 2025-12-XX | Role/group detection upgrade |
   ```
5. Proceed to FIX-005 (Ontology Granularity Expansion)

## Dependencies

- FIX-001: DEPLOYED (namespace/URI fixes)
- FIX-002: DEPLOYED (extractor unification, type normalization)
- FIX-003: DEPLOYED (ENTITY-default fix + pipeline ordering)
