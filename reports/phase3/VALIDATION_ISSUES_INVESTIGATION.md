# Knowledge Graph Quality Issues – Investigation

**Date**: 2025-12-10  
**Investigator**: Codex (GPT-5)  
**Trigger**: PROMPT_28 validation (grade B, issues found)  
**Current Registry State**: 13,227 entities, 43,909 mentions, dedup 69.88%, 0 type collisions, 0 placeholders

---

## Issue Overview (6 categories)
- **I1 – Bad-pattern residue**: ERC-1155, ERC-20, “Acceptance Criteria” still present.
- **I2 – Dedup initializer bug**: `graph_integration.py` missing `import os` blocked EntityResolver init (dedup disabled in pipeline mode).
- **I3 – Regen fragmentation**: 726 Regen-pattern rows; major variants unmerged (e.g., `regen` 658 mentions vs. `Regen Network` 2,733).
- **I4 – Gregory Landua fragmentation**: 15 variants totalling 421 mentions not collapsed to canonical.
- **I5 – DeSci fragmentation**: 14 variants totalling 641 mentions across org/product typos.
- **I6 – Type noise & tail**: 8,104 singletons; PERSON/ORG/PROJECT mislabels (generic groups, licenses, locations); CONCEPT coverage only 2 rows.

---

## Evidence & Root Cause Analysis

### I1 – Bad-pattern residue
- **SQL (PROMPT_28)**:  
  `SELECT entity_text, entity_type, occurrence_count FROM entity_registry WHERE entity_text ~ '^[A-Z]+-\\d+$' OR entity_text ILIKE '%acceptance criteria%' ...;` → ERC-1155 (2), ERC-20 (2), Acceptance Criteria (1).
- **Root cause**: Legacy rows predating PROMPT_24 cleanup; EntityQualityFilter blocks new ERC/JIRA/boilerplate but does not retro-delete existing rows.
- **Impact**: 3 entities, 5 mentions (minor but fails “zero bad patterns” criterion).

### I2 – Dedup initializer bug
- **Error (PROMPT_28 Task 5)**: `Failed to initialize EntityResolver: name 'os' is not defined`.
- **Root cause**: `src/knowledge_graph/graph_integration.py` used `os.getenv` but lacked `import os`, so dedup resolver never loaded when pipeline mode enabled.
- **Impact**: Tier 1/2 dedup disabled during graph integration; only doc-level dedup ran → more fragmentation.
- **Status**: Fixed locally by adding `import os` (keep in next deploy).

### I3 – Regen fragmentation
- **Counts**: `SELECT COUNT(*) FROM entity_registry WHERE entity_text ILIKE '%regen%';` → 726 rows; top variants: `Regen Network` 2,733 (ORG), `regen` 658 (ORG), `Regen` 209 (PROJECT), `$Regen` 191 (PROJECT), `$REGEN Coin` 118 (PROJECT), `Regen Commons` 218 (ORG), `Regen Ledger` 540 (TECH), etc.
- **Root causes**:
  - Tier-2 semantic threshold 0.95 misses “regen” → “Regen Network” (~0.85 sim).
  - Canonical mappings missing short/crypto variants.
  - EntityResolver disabled (I2) during integration.
- **Impact**: Hundreds of mentions split across variants; dedup rate stuck ~70% instead of 72-75%.

### I4 – Gregory Landua fragmentation
- **Counts**: `SELECT entity_text, entity_type, occurrence_count FROM entity_registry WHERE entity_text ILIKE '%landua%' OR entity_text ILIKE '%gregory%';` → 15 rows, 421 mentions; canonical `Gregory Landua` 262 vs. variants `Gregory_RND` 85, `Gregory` 47, `Gregory Regen` 10, `Gregory0` 7, `Landua` 4, etc.
- **Root causes**:
  - Threshold too high for partial names/usernames (0.70–0.85 sim).
  - No canonical alias list for Gregory Landua.
  - Dedup disabled (I2) during integration.
- **Impact**: 159+ mentions fragmented; PERSON accuracy degraded.

### I5 – DeSci fragmentation
- **Counts**: `SELECT entity_text, entity_type, occurrence_count FROM entity_registry WHERE entity_text ILIKE '%desci%';` → 14 rows, 641 mentions; `DeSci Labs AG` 334 (ORG), `DeSci Publish` 198 (PROJECT), `DeSci` 23, `DeSci Publi` 6 (typo), `DeSci Labs` 2, `DeSci Foundation` 10, etc.
- **Root causes**:
  - Missing canonical mappings for typos and shorthand.
  - Threshold 0.95 misses 0.85–0.9 sim pairs.
  - Dedup disabled (I2).
- **Impact**: 30–60 mentions fragmented; org/product split unclear.

### I6 – Type noise & tail
- **Tail noise**: `SELECT COUNT(*) FROM entity_registry WHERE occurrence_count = 1;` → 8,104 singletons.
- **Samples (PROMPT_28)**:
  - PERSON noise: “carbon credit buyers”, “Partners”, “water utilities”, “Credit Class Admins”, “Koi Project” (typed PERSON).
  - PROJECT noise: “UK”, “Apache License, Version 2.0”, “charaménez”.
  - CONCEPT coverage: only 2 rows (“Governance”, “Ecological Credit”).
- **Root causes**:
  - EntityQualityFilter lacks generic-group blocklist for PERSON.
  - Ontology gaps (no LOCATION/GROUP/LICENSE types; CONCEPT guidance thin).
  - Extraction prompt bias toward PROJECT/ORG.
  - High tail not pruned post-ingestion.
- **Impact**: Perceived quality hit; type distribution skewed (CLAIM 60%, CONCEPT 0.02%).

---

## Solutions (Quick vs. Root-Cause)

### I1 – Bad-pattern residue
- **Quick fix (SQL)**: Delete ERC*/Acceptance rows.
  ```sql
  DELETE FROM entity_registry
  WHERE entity_text ~ '^ERC-\\d+$'
     OR entity_text ILIKE '%ERC-Compatible%'
     OR entity_text ILIKE '%Acceptance Criteria%';
  ```
- **Root-cause**: None needed (filters already block new ones); add post-extract cleanup step to automation.

### I2 – Dedup initializer bug
- **Quick fix**: Add `import os` to `src/knowledge_graph/graph_integration.py` (already applied locally).
- **Root-cause**: Add smoke test in CI to instantiate `KnowledgeGraphIntegrator` with `enable_deduplication=True` to prevent regression.

### I3 – Regen fragmentation
- **Quick fix**: Add Regen aliases to `data/canonical_entities.json` and rerun `scripts/fix_entity_types.py --merge-only`.
  - Aliases: `regen`, `Regen`, `$Regen`, `$REGEN Coin`, `Regen Coin`, `Regen community` → canonical `Regen Network` (ORG) and `$Regen` (TOKEN/PROJECT if kept distinct).
- **Root-cause**:
  - Lower PERSON/ORG semantic threshold to 0.90–0.92 or add type-specific thresholds.
  - Enable Tier 1.5 canonical lookup before Tier 2 semantic match in EntityResolver.

### I4 – Gregory Landua fragmentation
- **Quick fix**: Canonical entry for “Gregory Landua” with aliases [`Gregory_RND`, `Gregory`, `Gregory Regen`, `Gregory0`, `Landua`, `glandua`, `Gregory Landau`, `@gregory_landua`, `Gregory | RND INC`, `G. Landua`] → rerun merge.
- **Root-cause**: Same as I3 (threshold tuning + Tier 1.5 canonical lookup).

### I5 – DeSci fragmentation
- **Quick fix**: Canonical entries:
  - `DeSci Labs AG` aliases [`DeSci Labs`, `DeSci`]
  - `DeSci Publish` aliases [`DeSci Publi`]
  - Decide if `DeSci` stands for movement (CONCEPT) vs org shorthand; map accordingly.
  - Rerun merge.
- **Root-cause**: Same threshold + canonical-first improvements; consider type override for typos to keep ORG vs PROJECT separation.

### I6 – Type noise & tail
- **Quick fixes (SQL)**:
  ```sql
  -- Re-type obvious mislabels
  UPDATE entity_registry SET entity_type='LOCATION'
    WHERE entity_text IN ('UK','US','EU','CA','AU') AND entity_type='PROJECT';
  UPDATE entity_registry SET entity_type='CONCEPT'
    WHERE entity_text ILIKE '%license%' AND entity_type='PROJECT';

  -- Delete generic PERSON groups
  DELETE FROM entity_registry
    WHERE entity_type='PERSON' AND entity_text IN
      ('Buyers','Partners','water utilities','carbon credit buyers','Credit Class Admins');
  ```
- **Root-cause fixes**:
  - Add generic-group blocklist to `EntityQualityFilter` (buyers/partners/users/utilities/etc.).
  - Expand ontology/type normalization for LOCATION/GROUP/LICENSE and CONCEPT guidance.
  - Update extraction prompts with explicit CONCEPT examples and “do not extract generic groups as PERSON”.
  - Consider pruning tail singletons after quality filters (keep if high confidence + typed).

---

## Recommended Execution Order
1) Apply code fix (I2) and restart/redeploy ingestion jobs.  
2) Run SQL cleanup for I1 and I6 quick fixes.  
3) Add canonical mappings (Regen, Gregory, DeSci) and rerun `scripts/fix_entity_types.py --merge-only` (Tier2 enabled).  
4) Tune dedup thresholds (type-specific 0.88–0.92) and add Tier 1.5 canonical lookup; rerun consolidation if adjusted.  
5) Enhance EntityQualityFilter + extraction prompts for type noise/CONCEPT coverage; re-extract high-value docs if needed.  
6) Re-run PROMPT_28 validation queries; target: dedup >72%, bad patterns 0, CONCEPT >0.5%, no obvious fragmentation.

---

## Validation After Fixes
- Re-run PROMPT_28 Task 1–3 SQL checks (stats, collisions, bad patterns, samples).  
- Check tail: `SELECT COUNT(*) FROM entity_registry WHERE occurrence_count=1;` (expect decrease).  
- Spot-check Regen/Gregory/DeSci queries to confirm consolidation.  
- Pipeline smoke test: instantiate `KnowledgeGraphIntegrator` with dedup on; run sample batch to confirm blocking works.  
- Fuseki triple count sanity (~102k) after any re-import.  

---

**Status**: Investigation complete. Actions above feed PROMPT_29 implementation plan.***
