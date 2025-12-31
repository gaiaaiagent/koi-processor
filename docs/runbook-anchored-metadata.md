# Runbook: Anchored Metadata Pipeline (Session E)

## Overview

This runbook covers testing and deployment of the off-chain metadata resolver + KOI caching + derivations system.

**Purpose**: Resolve Regen metadata IRIs via the allowlisted API, cache payloads with provenance, and extract derivable metrics (hectares first) only when backed by full citations.

## Prerequisites

- Access to production server: `ssh darren@202.61.196.119`
- PostgreSQL connection (eliza database)
- `bun` runtime installed

## Production Deployment Steps

### 1. Apply Database Migration

```bash
# SSH to production
ssh darren@202.61.196.119

# Navigate to koi-processor
cd /opt/projects/koi-processor

# Source environment
set -a && source .env && set +a

# Apply the migration
psql $POSTGRES_URL -f migrations/026_anchored_metadata_records.sql

# Verify tables created
psql $POSTGRES_URL -c "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'anchored%' OR table_name LIKE '%derivation%';"
```

Expected output:
```
        table_name
---------------------------
 anchored_metadata_records
 derivation_allowlist
 metadata_derivations
```

### 2. Verify Allowlist Seed

```bash
psql $POSTGRES_URL -c "SELECT metric_id, metric_label, is_active FROM derivation_allowlist;"
```

Expected output:
```
 metric_id |        metric_label        | is_active
-----------+----------------------------+-----------
 hectares  | Project Size (Hectares)    | t
```

> **Note**: tCO2e is intentionally NOT seeded - per plan, we don't emit tCO2e until explicit unit-bearing derivation exists.

### 3. Run Test Script

```bash
cd /opt/projects/koi-processor

# Run the test suite (uses sample IRIs)
bun run scripts/test-anchored-metadata.ts

# Or test with a specific known IRI:
bun run scripts/test-anchored-metadata.ts --iri "regen:YOUR_IRI_HERE.rdf"

# Force refresh to bypass cache:
bun run scripts/test-anchored-metadata.ts --iri "regen:YOUR_IRI_HERE.rdf" --force-refresh
```

### 4. Check Statistics

```bash
psql $POSTGRES_URL -c "SELECT * FROM anchored_metadata_stats;"
psql $POSTGRES_URL -c "SELECT * FROM derivation_stats;"
```

## Finding Valid IRIs to Test

To find real metadata IRIs from the Regen chain:

```bash
# Query a project's metadata IRI from the chain
# (Replace with actual project/class IDs from regen-1)

# Via KOI search (look for metadata references)
curl -s "https://regen.gaiaai.xyz/api/koi/query" \
  -H "Content-Type: application/json" \
  -d '{"query": "project metadata IRI", "limit": 5}' | jq '.data.results[].content' | head -20

# Or check ecocredit batches for metadata URIs
curl -s "https://regen.gaiaai.xyz/regen-api/ecocredits/batches?limit=3" | jq '.batches[].metadata'
```

## Troubleshooting

### Resolution Fails with 404

The Regen metadata resolver API may return 404 for:
- Invalid/malformed IRIs
- IRIs not registered with a resolver on-chain
- IRIs pointing to deleted/expired data

**Solution**: Verify the IRI is valid and has an active resolver on Regen mainnet.

### Resolution Fails with Timeout

Default timeout is 10 seconds.

**Solution**: Check network connectivity to `api.regen.network`. If the resolver is slow, consider increasing timeout in config.

### Derivation Returns No Metrics

The derivation system only extracts metrics defined in the allowlist AND requires:
- The JSON-LD payload to have the expected field path
- The unit to match the expected unit

**Solution**: Inspect the cached payload:
```sql
SELECT payload_jsonb FROM anchored_metadata_records WHERE iri = 'YOUR_IRI';
```

## Security Notes

- Only `api.regen.network` is allowlisted for fetching
- All IRIs must start with `regen:`
- Max payload size: 1MB
- Max IRI length: 256 characters
- No redirects followed
- All payloads hashed for integrity verification

## Integration Points (For Developers)

The system is exposed via:

```typescript
import { createAnchoredMetadataIntegration } from './src/metadata/metadata-integration.ts';

const integration = createAnchoredMetadataIntegration(pool);

// Extract hectares with full citation (enforces "no citation, no metric")
const result = await integration.extractHectaresWithCitation(iri);
if (result.success) {
  console.log(`Hectares: ${result.metric.value} ${result.metric.unit}`);
  console.log(`Citation: ${result.citation.resolver_url}`);
  // Use result.as_of_metadata for response envelope
}
```

## Metrics to Monitor

- `anchored_metadata_stats.total_records`: Total cached metadata records
- `anchored_metadata_stats.stale_records_7d`: Records older than 7 days (may need refresh)
- `derivation_stats.valid_derivations`: Successfully derived metrics
- `derivation_stats.invalid_derivations`: Failed validations (investigate)

## Rollback

To remove the anchored metadata system:

```sql
-- Drop tables (in order due to foreign keys)
DROP TABLE IF EXISTS metadata_derivations CASCADE;
DROP TABLE IF EXISTS derivation_allowlist CASCADE;
DROP TABLE IF EXISTS anchored_metadata_records CASCADE;

-- Drop views
DROP VIEW IF EXISTS anchored_metadata_stats;
DROP VIEW IF EXISTS derivation_stats;

-- Drop trigger function
DROP FUNCTION IF EXISTS update_anchored_metadata_updated_at;
```

## Definition of Done Checklist

- [ ] Migration applied successfully
- [ ] Test script passes IRI validation tests
- [ ] Test script connects to database
- [ ] At least one IRI can be resolved (or expected 404 for test IRIs)
- [ ] Caching works (second request faster and from_cache=true)
- [ ] `openapi-gpt.json` has NOT changed (verified)
- [ ] No new GPT-visible endpoints added (this is MCP-only/internal)
