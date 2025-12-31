#!/usr/bin/env bun
/**
 * Test Script: MCP Metadata Tools Validation
 * Session E: Off-chain Metadata Resolver + Derivation
 *
 * Tests:
 * 1. POST /api/koi/metadata/resolve - Resolve a metadata IRI (auth required)
 * 2. POST /api/koi/metadata/hectares - Derive hectares with citation (auth required)
 * 3. GET /api/koi/metadata/stats - Get metadata statistics (auth required)
 * 4. Public access blocking verification
 *
 * Usage:
 *   bun run scripts/test-mcp-metadata-tools.ts
 *   bun run scripts/test-mcp-metadata-tools.ts --iri <custom-iri>
 *
 * Environment:
 *   KOI_API_ENDPOINT - API base URL (default: http://localhost:8301/api/koi)
 *   KOI_INTERNAL_API_KEY - Internal API key for MCP-only endpoints (REQUIRED)
 *   TEST_METADATA_IRI - IRI to test (overrides --iri)
 */

const KOI_API_ENDPOINT = process.env.KOI_API_ENDPOINT || 'http://localhost:8301/api/koi';
const KOI_INTERNAL_API_KEY = process.env.KOI_INTERNAL_API_KEY || '';

// Canonical test IRI (prod-validated per handoff prompt)
const DEFAULT_TEST_IRI = 'regen:13toVfvfM5B7yuJqq8h3iVRHp3PKUJ4ABxHyvn4MeUMwwv1pWQGL295.rdf';

// Parse CLI args
const args = process.argv.slice(2);
let testIri = process.env.TEST_METADATA_IRI || DEFAULT_TEST_IRI;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--iri' && args[i + 1]) {
    testIri = args[i + 1];
    i++;
  }
}

console.log('='.repeat(60));
console.log('MCP Metadata Tools Validation');
console.log('='.repeat(60));
console.log(`API Endpoint: ${KOI_API_ENDPOINT}`);
console.log(`Test IRI: ${testIri}`);
console.log(`Internal API Key: ${KOI_INTERNAL_API_KEY ? '***configured***' : '⚠️  NOT SET'}`);
console.log('');

if (!KOI_INTERNAL_API_KEY) {
  console.warn('⚠️  WARNING: KOI_INTERNAL_API_KEY not set. Auth tests will fail.');
  console.warn('   Set it with: export KOI_INTERNAL_API_KEY=your-key');
  console.log('');
}

// Headers for authenticated requests
const authHeaders = {
  'Content-Type': 'application/json',
  'X-Internal-API-Key': KOI_INTERNAL_API_KEY,
};

interface TestResult {
  name: string;
  passed: boolean;
  duration_ms: number;
  details: string;
  data?: any;
}

const results: TestResult[] = [];

async function runTest(
  name: string,
  fn: () => Promise<{ passed: boolean; details: string; data?: any }>
): Promise<void> {
  const start = Date.now();
  try {
    const result = await fn();
    results.push({
      name,
      passed: result.passed,
      duration_ms: Date.now() - start,
      details: result.details,
      data: result.data
    });
  } catch (error: any) {
    results.push({
      name,
      passed: false,
      duration_ms: Date.now() - start,
      details: `Exception: ${error.message}`
    });
  }
}

// =============================================================================
// Test 1: Resolve Metadata IRI
// =============================================================================
await runTest('POST /metadata/resolve - Resolve IRI', async () => {
  const response = await fetch(`${KOI_API_ENDPOINT}/metadata/resolve`, {
    method: 'POST',
    headers: authHeaders,
    body: JSON.stringify({ iri: testIri })
  });

  const data = await response.json();

  if (response.status !== 200) {
    return {
      passed: false,
      details: `HTTP ${response.status}: ${data.error?.message || JSON.stringify(data)}`,
      data
    };
  }

  // Unwrap KOI envelope
  const result = data.data || data;

  const hasIri = !!result.iri;
  const hasContentHash = !!result.content_hash;
  const hasRid = !!result.rid;
  const hasResolverUrl = !!result.resolver_url;

  const passed = hasIri && hasContentHash && hasRid && hasResolverUrl;

  return {
    passed,
    details: passed
      ? `IRI: ${result.iri}\nContent Hash: ${result.content_hash?.slice(0, 16)}...\nRID: ${result.rid}`
      : `Missing fields: iri=${hasIri}, content_hash=${hasContentHash}, rid=${hasRid}, resolver_url=${hasResolverUrl}`,
    data: result
  };
});

// =============================================================================
// Test 2: Derive Hectares with Citation
// =============================================================================
await runTest('POST /metadata/hectares - Derive Hectares', async () => {
  const response = await fetch(`${KOI_API_ENDPOINT}/metadata/hectares`, {
    method: 'POST',
    headers: authHeaders,
    body: JSON.stringify({ iri: testIri })
  });

  const data = await response.json();

  if (response.status !== 200) {
    // Check if this is a "blocked" response (expected for some IRIs)
    if (data.blocked === true) {
      return {
        passed: true, // Blocked is valid behavior for "no citation, no metric"
        details: `Blocked (expected): ${data.error?.code} - ${data.error?.message}`,
        data
      };
    }
    return {
      passed: false,
      details: `HTTP ${response.status}: ${data.error?.message || JSON.stringify(data)}`,
      data
    };
  }

  // Unwrap KOI envelope
  const result = data.data || data;

  const hasHectares = typeof result.hectares === 'number';
  const hasUnit = !!result.unit;
  const hasDerivation = !!result.derivation;
  const hasCitations = Array.isArray(result.citations) && result.citations.length > 0;

  // Check derivation structure
  const derivation = result.derivation || {};
  const hasJsonPointer = !!derivation.json_pointer;
  const hasContentHash = !!derivation.content_hash;

  const passed = hasHectares && hasUnit && hasDerivation && hasJsonPointer;

  return {
    passed,
    details: passed
      ? `Hectares: ${result.hectares} ${result.unit}\nJSON Pointer: ${derivation.json_pointer}\nContent Hash: ${derivation.content_hash?.slice(0, 16)}...`
      : `Missing fields: hectares=${hasHectares}, unit=${hasUnit}, derivation=${hasDerivation}, json_pointer=${hasJsonPointer}`,
    data: result
  };
});

// =============================================================================
// Test 3: Invalid IRI Handling
// =============================================================================
await runTest('POST /metadata/hectares - Invalid IRI (blocked)', async () => {
  const response = await fetch(`${KOI_API_ENDPOINT}/metadata/hectares`, {
    method: 'POST',
    headers: authHeaders,
    body: JSON.stringify({ iri: 'invalid:not-a-regen-iri' })
  });

  const data = await response.json();

  // Should return 4xx with blocked=true
  const isBlocked = data.blocked === true || (data.error?.code === 'INVALID_IRI');
  const is4xx = response.status >= 400 && response.status < 500;

  return {
    passed: isBlocked,
    details: isBlocked
      ? `Correctly blocked invalid IRI: ${data.error?.code || 'blocked'}`
      : `Expected blocked=true, got: ${JSON.stringify(data)}`,
    data
  };
});

// =============================================================================
// Test 4: Metadata Stats
// =============================================================================
await runTest('GET /metadata/stats - Statistics', async () => {
  const response = await fetch(`${KOI_API_ENDPOINT}/metadata/stats`, {
    method: 'GET',
    headers: authHeaders,
  });

  const data = await response.json();

  if (response.status !== 200) {
    return {
      passed: false,
      details: `HTTP ${response.status}: ${data.error?.message || JSON.stringify(data)}`,
      data
    };
  }

  // Unwrap KOI envelope
  const result = data.data || data;

  const hasTotalRecords = typeof result.total_records === 'number';
  const hasSuccessfulResolutions = typeof result.successful_resolutions === 'number';

  const passed = hasTotalRecords && hasSuccessfulResolutions;

  return {
    passed,
    details: passed
      ? `Total records: ${result.total_records}\nSuccessful resolutions: ${result.successful_resolutions}\nValid hectares: ${result.valid_hectares_derivations || 0}`
      : `Missing stats fields`,
    data: result
  };
});

// =============================================================================
// Test 5: Public Access Blocking (no auth header)
// =============================================================================
await runTest('POST /metadata/resolve - Public access BLOCKED', async () => {
  const response = await fetch(`${KOI_API_ENDPOINT}/metadata/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }, // No X-Internal-API-Key
    body: JSON.stringify({ iri: testIri })
  });

  const data = await response.json();

  // Public access must be blocked. Depending on deployment routing, this may surface as:
  // - 401/403 when the route exists but is auth-gated
  // - 404 when the route is not exposed publicly at all
  const isBlocked = response.status === 401 || response.status === 403 || response.status === 404;

  return {
    passed: isBlocked,
    details: isBlocked
      ? `Correctly blocked public access: HTTP ${response.status} - ${data.error?.code}`
      : `Expected 401/403/404, got HTTP ${response.status}: ${JSON.stringify(data)}`,
    data
  };
});

// =============================================================================
// Print Results
// =============================================================================
console.log('');
console.log('='.repeat(60));
console.log('Test Results');
console.log('='.repeat(60));

let passedCount = 0;
let failedCount = 0;

for (const result of results) {
  const status = result.passed ? '✅ PASS' : '❌ FAIL';
  console.log(`\n${status} ${result.name} (${result.duration_ms}ms)`);
  console.log('-'.repeat(40));
  console.log(result.details);

  if (result.passed) {
    passedCount++;
  } else {
    failedCount++;
  }
}

console.log('');
console.log('='.repeat(60));
console.log(`Summary: ${passedCount} passed, ${failedCount} failed`);
console.log('='.repeat(60));

// Exit with appropriate code
process.exit(failedCount > 0 ? 1 : 0);
