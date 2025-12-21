# Agent Task: FIX-005 - Ontology Granularity Expansion

## Context

FIX-001 through FIX-004 are DEPLOYED. FIX-005 is the final P2 fix before Stage 6 re-extraction (30,904 documents).

**Problem:** The current type system is too coarse and omits key domain entities. This drives mis-typing, missing entities, and dedup instability.

**Evidence:**
- Credit classes typed as CONCEPT/PROJECT/ENTITY
- Governance proposals typed as CONCEPT/EVENT
- Cosmos SDK modules typed as TECHNOLOGY/PROJECT
- Msg* API messages blocked as "technical patterns"
- ≥98 errors + 32 missing entity IDs in registry

## Objective

Expand the ontology with domain-specific and general types:

**Domain Types (Regen/Cosmos):**
- `CREDIT_CLASS` - Carbon/eco credit classes (e.g., "C01", "CarbonPlus Grasslands")
- `GOVERNANCE_PROPOSAL` - On-chain governance proposals
- `VALIDATOR` - Blockchain validators
- `MODULE` - Cosmos SDK modules (e.g., "x/ecocredit", "x/group")
- `API_MESSAGE` - Protobuf message types (e.g., "MsgSend", "MsgCreateBatch")
- `KEEPER` - Cosmos SDK keeper interfaces

**General Types:**
- `LICENSE` - Software/content licenses (e.g., "Apache 2.0", "CC BY-SA")
- `STANDARD` - Standards/specifications (e.g., "ISO 14064", "Verra VM0042")
- `PROCESS` - Business/technical processes
- `MATERIAL` - Physical materials/resources

## Guardrails

- Implement code + tests only. Do NOT run full re-extraction.
- Do NOT run against production DB/Fuseki unless explicitly instructed.
- Do NOT run data migration SQL (Stage 6 re-extraction will clean data).
- Coordinate type additions across ALL files in sync (don't leave partial state).
- On the production server, prefer running tests via an existing repo venv (`/opt/projects/koi-processor/.venv`) because the system Python environment is PEP-668 “externally managed” (pip install is blocked).

## Repo Paths

- Server: /opt/projects/koi-processor (production)
- Local: /Users/darrenzal/projects/RegenAI/koi-processor
- Documentation: /Users/darrenzal/projects/RegenAI/knowledge-graph-review-2025-12.md

## Read First (required)

| File | Purpose |
|------|---------|
| `src/core/entity_types.py` | Canonical types, LLM_ALLOWED_TYPES, TYPE_ALIASES_TO_CANONICAL |
| `src/extraction/prompt_builder.py` | Shared extraction prompt (type guidance) |
| `src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py` | Type normalization mappings |
| `src/knowledge_graph/uri_generator.py` | TYPE_PREFIXES for deterministic URIs |
| `src/knowledge_graph/improvements/entity_quality_filter.py` | May need to allow domain identifiers |

## Implementation Steps

### Step 1: Expand entity_types.py

**1A. Add new canonical types to `ALL_CANONICAL_TYPES` (lines 22-35):**

```python
ALL_CANONICAL_TYPES: Set[str] = {
    # Existing types
    "ENTITY",        # Fallback default for unknown types
    "PERSON",        # Named individuals with proper names
    "ORGANIZATION",  # Companies, foundations, networks, institutions
    "PROJECT",       # Named initiatives, platforms (non-software)
    "CONCEPT",       # Abstract ideas, methodologies, frameworks
    "TECHNOLOGY",    # Technical systems, tools, AI systems, software
    "CLAIM",         # Assertions and statements
    "EVIDENCE",      # Supporting data and proof
    "QUESTION",      # Questions and inquiries
    "LOCATION",      # Geographic places (countries, cities, regions)
    "EVENT",         # Named events (calls, conferences, workshops)
    "FUNCTION",      # Code functions (code graph only, NOT LLM-allowed)

    # FIX-005: Domain types (Regen/Cosmos)
    "CREDIT_CLASS",        # Carbon/eco credit classes (C01, CarbonPlus)
    "GOVERNANCE_PROPOSAL", # On-chain governance proposals
    "VALIDATOR",           # Blockchain validators
    "MODULE",              # Cosmos SDK modules (x/ecocredit, x/group)
    "API_MESSAGE",         # Protobuf message types (MsgSend, MsgCreateBatch)
    "KEEPER",              # Cosmos SDK keeper interfaces

    # FIX-005: General types
    "LICENSE",     # Software/content licenses
    "STANDARD",    # Standards/specifications (ISO, Verra)
    "PROCESS",     # Business/technical processes
    "MATERIAL",    # Physical materials/resources
}
```

**1B. Add new types to `LLM_ALLOWED_TYPES` (lines 44-55):**

```python
LLM_ALLOWED_TYPES: Set[str] = {
    # Existing
    "PERSON",
    "ORGANIZATION",
    "PROJECT",
    "CONCEPT",
    "TECHNOLOGY",
    "CLAIM",
    "EVIDENCE",
    "QUESTION",
    "LOCATION",
    "EVENT",

    # FIX-005: Domain types
    "CREDIT_CLASS",
    "GOVERNANCE_PROPOSAL",
    "VALIDATOR",
    "MODULE",
    "API_MESSAGE",
    "KEEPER",

    # FIX-005: General types
    "LICENSE",
    "STANDARD",
    "PROCESS",
    "MATERIAL",
}
```

**1C. Update `TYPE_ALIASES_TO_CANONICAL` (add new mappings, REMOVE LICENSE/STANDARD from CONCEPT aliases):**

```python
# REMOVE these lines (LICENSE and STANDARD are now canonical types):
# "STANDARD": "CONCEPT",
# "LICENSE": "CONCEPT",

# ADD these new alias mappings:
TYPE_ALIASES_TO_CANONICAL.update({
    # CREDIT_CLASS aliases
    "CREDITCLASS": "CREDIT_CLASS",
    "CREDIT": "CREDIT_CLASS",
    "ECOCREDIT": "CREDIT_CLASS",
    "ECO_CREDIT": "CREDIT_CLASS",

    # GOVERNANCE_PROPOSAL aliases
    "GOVERNANCEPROPOSAL": "GOVERNANCE_PROPOSAL",
    "PROPOSAL": "GOVERNANCE_PROPOSAL",
    "GOV_PROPOSAL": "GOVERNANCE_PROPOSAL",

    # VALIDATOR aliases
    "BLOCKVALIDATOR": "VALIDATOR",
    "BLOCK_VALIDATOR": "VALIDATOR",

    # MODULE aliases
    "COSMOS_MODULE": "MODULE",
    "SDK_MODULE": "MODULE",

    # API_MESSAGE aliases
    "MESSAGE": "API_MESSAGE",
    "MSG": "API_MESSAGE",
    "PROTOBUF_MESSAGE": "API_MESSAGE",

    # KEEPER aliases
    "SDK_KEEPER": "KEEPER",

    # PROCESS aliases
    "WORKFLOW": "PROCESS",
    "PROCEDURE": "PROCESS",  # Note: Was FUNCTION alias, now PROCESS

    # MATERIAL aliases
    "RESOURCE": "MATERIAL",
    "SUBSTANCE": "MATERIAL",
})
```

**1D. Update `get_canonical_description()` (lines 253-278):**

Add descriptions for new types:
```python
descriptions.update({
    "CREDIT_CLASS": "Carbon/eco credit classes and certification types",
    "GOVERNANCE_PROPOSAL": "On-chain governance proposals",
    "VALIDATOR": "Blockchain validators and validator operators",
    "MODULE": "Cosmos SDK modules (x/ecocredit, x/group, etc.)",
    "API_MESSAGE": "Protobuf/API message types (MsgSend, etc.)",
    "KEEPER": "Cosmos SDK keeper interfaces",
    "LICENSE": "Software and content licenses",
    "STANDARD": "Standards and specifications (ISO, Verra, etc.)",
    "PROCESS": "Business and technical processes",
    "MATERIAL": "Physical materials and resources",
})
```

### Step 2: Update prompt_builder.py

Add type guidance for new types after the existing rules (around line 100):

```python
### CREDIT_CLASS: Regen Network credit classes
Use CREDIT_CLASS for carbon/eco credit classifications:
- "C01", "C02", "C03" (credit class IDs)
- "CarbonPlus Grasslands", "Wilmot Cattle Grazing" (credit class names)
- "Verified Carbon Standard", "Gold Standard" (certification programs)
NOT for: Organizations that issue credits (those are ORGANIZATION)

### GOVERNANCE_PROPOSAL: On-chain proposals
Use GOVERNANCE_PROPOSAL for blockchain governance proposals:
- "Proposal 47", "Signaling Proposal: Community Pool Spend"
- "Parameter Change Proposal", "Text Proposal"
NOT for: General ideas or suggestions (those are CONCEPT)

### VALIDATOR: Blockchain validators
Use VALIDATOR for blockchain validator operators:
- "Regen Validator", "Chorus One", "Figment"
- Validator node operators and their infrastructure
NOT for: The "validator" role term (blocked by FIX-004)

### MODULE: Cosmos SDK modules
Use MODULE for Cosmos SDK/blockchain modules:
- "x/ecocredit", "x/group", "x/data", "x/staking"
- "EcocreditModule", "GroupModule"
NOT for: General software modules (those are TECHNOLOGY)

### API_MESSAGE: Protobuf message types
Use API_MESSAGE for Cosmos SDK message types:
- "MsgSend", "MsgCreateBatch", "MsgRetire"
- "MsgVote", "MsgDelegate", "MsgSubmitProposal"
NOT for: General API endpoints (those are TECHNOLOGY)

### KEEPER: Cosmos SDK keepers
Use KEEPER for SDK keeper interfaces:
- "EcocreditKeeper", "GroupKeeper", "BankKeeper"
NOT for: The "keeper" role term (blocked by FIX-004)

### LICENSE: Software/content licenses
Use LICENSE for licensing terms:
- "Apache 2.0", "MIT License", "GPL-3.0"
- "CC BY-SA 4.0", "Creative Commons"
NOT for: Organizations that create licenses (those are ORGANIZATION)

### STANDARD: Technical standards
Use STANDARD for specifications and standards:
- "ISO 14064", "ISO 14067", "GHG Protocol"
- "Verra VM0042", "VCS Standard"
NOT for: Organizations that publish standards (those are ORGANIZATION)

### PROCESS: Business/technical processes
Use PROCESS for named processes:
- "MRV Process", "Verification Process"
- "Credit Issuance Workflow"
NOT for: Actions or verbs (skip those)

### MATERIAL: Physical materials
Use MATERIAL for physical substances:
- "Biochar", "Biomass", "Soil Carbon"
- "Organic Matter", "Compost"
NOT for: Abstract concepts (those are CONCEPT)
```

### Step 3: Update ontology_normalizer_module.py

Extend `DEFAULT_TYPE_MAPPINGS` (around lines 31-76):

```python
# IMPORTANT: Remove/replace conflicting legacy mappings first:
# - DEFAULT_TYPE_MAPPINGS currently maps 'MODULE' -> 'PROJECT' (Project variations).
#   FIX-005 introduces a distinct canonical MODULE type, so 'MODULE' must normalize to 'MODULE' (not PROJECT).

DEFAULT_TYPE_MAPPINGS.update({
    # FIX-005: Credit class variations
    'CREDITCLASS': 'CREDIT_CLASS',
    'CREDIT_CLASS': 'CREDIT_CLASS',
    'ECOCREDIT': 'CREDIT_CLASS',
    'ECO_CREDIT': 'CREDIT_CLASS',

    # FIX-005: Governance proposal variations
    'GOVERNANCEPROPOSAL': 'GOVERNANCE_PROPOSAL',
    'GOVERNANCE_PROPOSAL': 'GOVERNANCE_PROPOSAL',
    'PROPOSAL': 'GOVERNANCE_PROPOSAL',
    'GOV_PROPOSAL': 'GOVERNANCE_PROPOSAL',

    # FIX-005: Module variations
    'COSMOS_MODULE': 'MODULE',
    'SDK_MODULE': 'MODULE',

    # FIX-005: API message variations
    'MESSAGE': 'API_MESSAGE',
    'API_MESSAGE': 'API_MESSAGE',
    'MSG': 'API_MESSAGE',
    'PROTOBUF_MESSAGE': 'API_MESSAGE',

    # FIX-005: Keeper variations
    'KEEPER': 'KEEPER',
    'SDK_KEEPER': 'KEEPER',

    # FIX-005: License variations
    'LICENSE': 'LICENSE',
    'SOFTWARE_LICENSE': 'LICENSE',

    # FIX-005: Standard variations
    'STANDARD': 'STANDARD',
    'SPECIFICATION': 'STANDARD',

    # FIX-005: Process variations
    'WORKFLOW': 'PROCESS',
    'PROCEDURE': 'PROCESS',

    # FIX-005: Material variations
    'RESOURCE': 'MATERIAL',
    'SUBSTANCE': 'MATERIAL',
})

# Also add new canonical types to the module's type list for documentation
CANONICAL_TYPES = {
    'PERSON', 'ORGANIZATION', 'PROJECT', 'CONCEPT', 'TECHNOLOGY',
    'LOCATION', 'EVENT', 'CLAIM', 'EVIDENCE', 'QUESTION',
    # FIX-005 additions
    'CREDIT_CLASS', 'GOVERNANCE_PROPOSAL', 'VALIDATOR', 'MODULE',
    'API_MESSAGE', 'KEEPER', 'LICENSE', 'STANDARD', 'PROCESS', 'MATERIAL',
}
```

### Step 4: Update uri_generator.py

Extend `TYPE_PREFIXES` (lines 24-39):

```python
TYPE_PREFIXES = {
    # Existing prefixes
    "PERSON": "person",
    "ORGANIZATION": "org",
    "PROJECT": "project",
    "LOCATION": "location",
    "EVENT": "event",
    "CONCEPT": "concept",
    "CLAIM": "claim",
    "TECHNOLOGY": "tech",
    "METHODOLOGY": "methodology",
    "METRIC": "metric",
    "PRODUCT": "product",
    "DOCUMENT": "doc",
    "STANDARD": "standard",
    "PROTOCOL": "protocol",

    # FIX-005: Domain types
    "CREDIT_CLASS": "credit-class",
    "GOVERNANCE_PROPOSAL": "proposal",
    "VALIDATOR": "validator",
    "MODULE": "module",
    "API_MESSAGE": "msg",
    "KEEPER": "keeper",

    # FIX-005: General types
    "LICENSE": "license",
    "PROCESS": "process",
    "MATERIAL": "material",
    # STANDARD already exists above
}
```

### Step 5: Update entity_quality_filter.py (if needed)

Allow domain identifiers that were previously blocked as "technical patterns".

In `is_technical_pattern()` or similar method, add whitelist for domain terms:

```python
# FIX-005: Allow legitimate domain identifiers
DOMAIN_IDENTIFIER_PATTERNS = [
    # Cosmos SDK messages (MsgSend, MsgCreateBatch, etc.)
    re.compile(r'^Msg[A-Z][a-zA-Z]+$'),
    # Credit class IDs (C01, C02, etc.)
    re.compile(r'^C\d{2,3}$'),
    # Module paths (x/ecocredit, x/group)
    re.compile(r'^x/[a-z]+$'),
]

def is_allowed_domain_identifier(self, name: str, entity_type: str) -> bool:
    """Check if name is a legitimate domain identifier that should NOT be blocked."""
    if entity_type.upper() not in ('API_MESSAGE', 'MODULE', 'CREDIT_CLASS'):
        return False

    for pattern in self.DOMAIN_IDENTIFIER_PATTERNS:
        if pattern.match(name):
            return True
    return False
```

Then in the filter flow, check this before blocking:

```python
# Don't block legitimate domain identifiers
if self.is_allowed_domain_identifier(name, entity_type):
    return True, ""  # Pass through
```

Also add *anti-generic* guardrails for new domain types to prevent the LLM from turning role words into entities:

```python
def is_generic_domain_type_word(self, name: str, entity_type: str) -> bool:
    """Block generic role/type words even if the LLM assigns a domain type."""
    t = (entity_type or "").upper()
    n = (name or "").strip().lower()

    if t == "VALIDATOR" and n in {"validator", "validators"}:
        return True
    if t == "KEEPER" and n in {"keeper", "keepers"}:
        return True
    if t == "MODULE" and n in {"module", "modules"}:
        return True
    if t == "API_MESSAGE" and n in {"message", "messages", "msg"}:
        return True
    if t == "GOVERNANCE_PROPOSAL" and n in {"proposal", "proposals"}:
        return True
    return False
```

Integrate this check early in `filter_entity()` / `filter_with_reasons()` for the relevant entity types.

## Testing Plan

### Unit Tests

Create `tests/test_fix005_ontology_expansion.py`:

```python
import pytest
from core.entity_types import (
    ALL_CANONICAL_TYPES, LLM_ALLOWED_TYPES,
    TYPE_ALIASES_TO_CANONICAL, normalize_type, is_llm_allowed_type
)


class TestFix005OntologyExpansion:
    """FIX-005: Ontology granularity expansion tests."""

    # ========================================================================
    # Test new canonical types exist
    # ========================================================================

    def test_domain_types_in_canonical(self):
        """Domain types should be in ALL_CANONICAL_TYPES."""
        domain_types = {
            "CREDIT_CLASS", "GOVERNANCE_PROPOSAL", "VALIDATOR",
            "MODULE", "API_MESSAGE", "KEEPER"
        }
        for t in domain_types:
            assert t in ALL_CANONICAL_TYPES, f"{t} not in ALL_CANONICAL_TYPES"

    def test_general_types_in_canonical(self):
        """General types should be in ALL_CANONICAL_TYPES."""
        general_types = {"LICENSE", "STANDARD", "PROCESS", "MATERIAL"}
        for t in general_types:
            assert t in ALL_CANONICAL_TYPES, f"{t} not in ALL_CANONICAL_TYPES"

    def test_new_types_llm_allowed(self):
        """New types should be in LLM_ALLOWED_TYPES."""
        new_types = {
            "CREDIT_CLASS", "GOVERNANCE_PROPOSAL", "VALIDATOR",
            "MODULE", "API_MESSAGE", "KEEPER",
            "LICENSE", "STANDARD", "PROCESS", "MATERIAL"
        }
        for t in new_types:
            assert t in LLM_ALLOWED_TYPES, f"{t} not in LLM_ALLOWED_TYPES"
            assert is_llm_allowed_type(t), f"is_llm_allowed_type({t}) returned False"

    # ========================================================================
    # Test type normalization
    # ========================================================================

    def test_normalize_credit_class_aliases(self):
        """Credit class aliases should normalize correctly."""
        assert normalize_type("CREDITCLASS") == "CREDIT_CLASS"
        assert normalize_type("creditclass") == "CREDIT_CLASS"
        assert normalize_type("ECOCREDIT") == "CREDIT_CLASS"
        assert normalize_type("eco_credit") == "CREDIT_CLASS"

    def test_normalize_governance_proposal_aliases(self):
        """Governance proposal aliases should normalize correctly."""
        assert normalize_type("GOVERNANCEPROPOSAL") == "GOVERNANCE_PROPOSAL"
        assert normalize_type("PROPOSAL") == "GOVERNANCE_PROPOSAL"
        assert normalize_type("gov_proposal") == "GOVERNANCE_PROPOSAL"

    def test_normalize_api_message_aliases(self):
        """API message aliases should normalize correctly."""
        assert normalize_type("MESSAGE") == "API_MESSAGE"
        assert normalize_type("MSG") == "API_MESSAGE"
        assert normalize_type("protobuf_message") == "API_MESSAGE"

    def test_normalize_license_not_concept(self):
        """LICENSE should be its own type, not CONCEPT."""
        assert normalize_type("LICENSE") == "LICENSE"
        assert normalize_type("license") == "LICENSE"
        assert normalize_type("LICENSE") != "CONCEPT"

    def test_normalize_standard_not_concept(self):
        """STANDARD should be its own type, not CONCEPT."""
        assert normalize_type("STANDARD") == "STANDARD"
        assert normalize_type("standard") == "STANDARD"
        assert normalize_type("STANDARD") != "CONCEPT"

    # ========================================================================
    # Test URI generation
    # ========================================================================

    def test_uri_prefixes_for_new_types(self):
        """New types should have URI prefixes."""
        from knowledge_graph.uri_generator import DeterministicURIGenerator

        gen = DeterministicURIGenerator()

        # Check domain types have prefixes (not fallback to "entity")
        assert "credit-class" in gen.generate_uri("C01", "CREDIT_CLASS")
        assert "proposal" in gen.generate_uri("Proposal 47", "GOVERNANCE_PROPOSAL")
        assert "validator" in gen.generate_uri("Chorus One", "VALIDATOR")
        assert "module" in gen.generate_uri("x/ecocredit", "MODULE")
        assert "msg" in gen.generate_uri("MsgSend", "API_MESSAGE")
        assert "keeper" in gen.generate_uri("EcocreditKeeper", "KEEPER")

        # Check general types
        assert "license" in gen.generate_uri("Apache 2.0", "LICENSE")
        assert "standard" in gen.generate_uri("ISO 14064", "STANDARD")
        assert "process" in gen.generate_uri("MRV Process", "PROCESS")
        assert "material" in gen.generate_uri("Biochar", "MATERIAL")

    # ========================================================================
    # Test ontology normalizer
    # ========================================================================

    def test_ontology_normalizer_new_mappings(self):
        """OntologyNormalizer should handle new type mappings."""
        from knowledge_graph.postprocessing.modules.ontology_normalizer_module import (
            OntologyNormalizerModule
        )

        normalizer = OntologyNormalizerModule()

        assert normalizer.get_canonical_type("CREDITCLASS") == "CREDIT_CLASS"
        assert normalizer.get_canonical_type("GOVERNANCEPROPOSAL") == "GOVERNANCE_PROPOSAL"
        assert normalizer.get_canonical_type("MESSAGE") == "API_MESSAGE"
        assert normalizer.get_canonical_type("WORKFLOW") == "PROCESS"

    # ========================================================================
    # Test entity quality filter allows domain identifiers
    # ========================================================================

    def test_allows_msg_types_as_api_message(self):
        """Msg* types should be allowed when typed as API_MESSAGE."""
        from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter

        f = EntityQualityFilter()

        # MsgSend as API_MESSAGE should pass
        passes, reason = f.filter_entity({"name": "MsgSend", "type": "API_MESSAGE"})
        assert passes is True, f"MsgSend blocked: {reason}"

        passes, reason = f.filter_entity({"name": "MsgCreateBatch", "type": "API_MESSAGE"})
        assert passes is True, f"MsgCreateBatch blocked: {reason}"

    def test_allows_credit_class_ids(self):
        """Credit class IDs should be allowed when typed as CREDIT_CLASS."""
        from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter

        f = EntityQualityFilter()

        passes, reason = f.filter_entity({"name": "C01", "type": "CREDIT_CLASS"})
        assert passes is True, f"C01 blocked: {reason}"

        passes, reason = f.filter_entity({"name": "C02", "type": "CREDIT_CLASS"})
        assert passes is True, f"C02 blocked: {reason}"

    def test_allows_module_paths(self):
        """Module paths should be allowed when typed as MODULE."""
        from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter

        f = EntityQualityFilter()

        passes, reason = f.filter_entity({"name": "x/ecocredit", "type": "MODULE"})
        assert passes is True, f"x/ecocredit blocked: {reason}"

        passes, reason = f.filter_entity({"name": "x/group", "type": "MODULE"})
        assert passes is True, f"x/group blocked: {reason}"

    def test_blocks_generic_domain_words_even_when_typed(self):
        """Generic role words should be blocked even if mis-typed as domain types."""
        from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter

        f = EntityQualityFilter()

        assert f.filter_entity({"name": "validators", "type": "VALIDATOR"})[0] is False
        assert f.filter_entity({"name": "keepers", "type": "KEEPER"})[0] is False
        assert f.filter_entity({"name": "modules", "type": "MODULE"})[0] is False
        assert f.filter_entity({"name": "proposal", "type": "GOVERNANCE_PROPOSAL"})[0] is False
```

### Integration Test

```python
def test_full_extraction_uses_new_types():
    """Extraction pipeline should use new types correctly."""
    from extraction.prompt_builder import build_extraction_prompt, LLM_ALLOWED_TYPES

    # Verify new types in prompt
    prompt = build_extraction_prompt("Test content", "discourse")

    # Check domain types mentioned
    assert "CREDIT_CLASS" in prompt or "credit" in prompt.lower()
    assert "API_MESSAGE" in prompt or "Msg" in prompt

    # Check new types are LLM-allowed
    new_types = ["CREDIT_CLASS", "LICENSE", "STANDARD", "API_MESSAGE"]
    for t in new_types:
        assert t in LLM_ALLOWED_TYPES
```

### Validation Queries (run after Stage 6 re-extraction)

```sql
-- Check entities now using new types
SELECT entity_type, COUNT(*) as count
FROM entity_registry
WHERE entity_type IN (
    'CREDIT_CLASS', 'GOVERNANCE_PROPOSAL', 'VALIDATOR',
    'MODULE', 'API_MESSAGE', 'KEEPER',
    'LICENSE', 'STANDARD', 'PROCESS', 'MATERIAL'
)
GROUP BY entity_type
ORDER BY count DESC;

-- Check Msg* types are now API_MESSAGE (not TECHNOLOGY)
SELECT entity_text, entity_type
FROM entity_registry
WHERE entity_text ~* '^Msg[A-Z]'
ORDER BY entity_text;
-- Expected: type = API_MESSAGE

-- Check credit classes are now CREDIT_CLASS (not CONCEPT/PROJECT)
SELECT entity_text, entity_type
FROM entity_registry
WHERE entity_text ~* 'Credit Class$|^C\d{2,3}$'
ORDER BY entity_text;
-- Expected: type = CREDIT_CLASS
```

## Success Criteria

- [ ] **CRITICAL**: `OntologyNormalizerModule.DEFAULT_TYPE_MAPPINGS` no longer maps `'MODULE'` → `'PROJECT'` (remove/overwrite the legacy mapping at line ~55)
- [ ] New types added to `ALL_CANONICAL_TYPES` (10 new types)
- [ ] New types added to `LLM_ALLOWED_TYPES` (10 new types)
- [ ] LICENSE and STANDARD removed from CONCEPT aliases
- [ ] New type aliases work (CREDITCLASS -> CREDIT_CLASS, etc.)
- [ ] URI generator has prefixes for all new types (no "entity" fallback)
- [ ] OntologyNormalizer maps new type variations correctly
- [ ] Entity quality filter allows domain identifiers (MsgSend, C01, x/ecocredit)
- [ ] Prompt builder includes guidance for new types
- [ ] All unit tests pass
- [ ] No regression in existing tests (run full KG regression suite)

## Do NOT

- Do NOT run data migration SQL (Stage 6 re-extraction will clean data)
- Do NOT modify FIX-001/FIX-002/FIX-003/FIX-004 code (already deployed)
- Do NOT change database schema
- Do NOT push to production without running tests locally first
- Do NOT run full re-extraction as part of FIX-005

## After Completion

1. Run tests locally:
   ```bash
   cd /Users/darrenzal/projects/RegenAI/koi-processor
   PYTHONPATH=src pytest tests/test_fix005_ontology_expansion.py -v
   # Full KG regression suite
   PYTHONPATH=src pytest -q \
     tests/test_fix002_extractor_contract.py \
     tests/test_pipeline_modules.py \
     tests/test_fix003_entity_validation.py \
     tests/test_fix004_role_detection.py \
     tests/test_fix005_ontology_expansion.py
   ```

2. Commit locally:
   ```bash
   git add -A
   git commit -m "feat(kg-quality): FIX-005 ontology granularity expansion

   - Add 10 new canonical types: CREDIT_CLASS, GOVERNANCE_PROPOSAL,
     VALIDATOR, MODULE, API_MESSAGE, KEEPER, LICENSE, STANDARD,
     PROCESS, MATERIAL
   - Update LLM_ALLOWED_TYPES with new types
   - Add type aliases for common variations
   - Update prompt builder with type guidance
   - Update ontology normalizer with new mappings
   - Update URI generator with new prefixes
   - Allow domain identifiers in entity filter

   Tests: XX passing (FIX-005 + KG regression suite)"
   ```

3. Deploy to server:
   ```bash
   git push origin regen-prod
   ssh darren@202.61.196.119 'cd /opt/projects/koi-processor && git pull'
   ```

4. Run tests on server:
   ```bash
   ssh darren@202.61.196.119 'cd /opt/projects/koi-processor && \
     PYTHONPATH=src ./.venv/bin/pytest -q tests/test_fix005_ontology_expansion.py'
   ```

5. Update status table in `knowledge-graph-review-2025-12.md`:
   ```
   | FIX-005 | DEPLOYED | ...koi-processor | 2025-12-XX | Ontology granularity expansion |
   ```

6. Proceed to Stage 6 re-extraction (all P1-P2 fixes complete)

## Dependencies

- FIX-001: DEPLOYED (namespace/URI fixes)
- FIX-002: DEPLOYED (extractor unification, type normalization)
- FIX-003: DEPLOYED (ENTITY-default fix + pipeline ordering)
- FIX-004: DEPLOYED (role/group detection upgrade)

## Notes

- LICENSE and STANDARD were previously aliased to CONCEPT. FIX-005 promotes them to canonical types.
- PROCEDURE was aliased to FUNCTION (code graph). FIX-005 reassigns it to PROCESS.
- The "keeper" and "validator" role terms are still blocked by FIX-004's GENERIC_GROUP_TERMS when typed as PERSON. They're only allowed when properly typed as KEEPER or VALIDATOR.
