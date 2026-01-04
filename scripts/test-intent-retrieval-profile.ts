#!/usr/bin/env bun
/**
 * Regression Test: Intent-Aware Retrieval Profile MVP
 * Session KOI: "mention ≠ evidence" MVP
 *
 * Tests the person_activity intent to ensure:
 * 1. profile_debug fields are present in response
 * 2. koi-sensors repo is excluded from citations
 * 3. answerable/answerability_reason are set correctly
 *
 * Usage:
 *   bun run scripts/test-intent-retrieval-profile.ts [--api-url <url>]
 *
 * Environment:
 *   KOI_API_URL - API endpoint (default: http://localhost:8301)
 */

// =============================================================================
// Configuration
// =============================================================================

const DEFAULT_API_URL = 'http://localhost:8301';
const API_URL = process.env.KOI_API_URL || DEFAULT_API_URL;

// Parse command-line arguments
const args = process.argv.slice(2);
let apiUrl = API_URL;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--api-url' && args[i + 1]) {
    apiUrl = args[i + 1];
    i++;
  }
}

// =============================================================================
// Test Utilities
// =============================================================================

interface TestResult {
  name: string;
  passed: boolean;
  message: string;
  details?: any;
}

function logResult(result: TestResult): void {
  const icon = result.passed ? '✅' : '❌';
  console.log(`${icon} ${result.name}: ${result.message}`);
  if (!result.passed && result.details) {
    console.log('   Details:', JSON.stringify(result.details, null, 2));
  }
}

// =============================================================================
// Test Cases
// =============================================================================

async function testPersonActivityQuery(): Promise<TestResult[]> {
  const results: TestResult[] = [];
  const endpoint = `${apiUrl}/api/koi/query`;

  console.log(`\n📍 Testing: POST ${endpoint}`);
  console.log(`   Query: "What is Greg Landua working on?"`);
  console.log(`   Intent: person_activity\n`);

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: 'What is Greg Landua working on?',
        intent: 'person_activity',
        limit: 10,
      }),
    });

    if (!response.ok) {
      results.push({
        name: 'API Response Status',
        passed: false,
        message: `HTTP ${response.status}: ${response.statusText}`,
      });
      return results;
    }

    const envelope = await response.json();
    const data = envelope.data;

    // Test 1: Check that profile_debug exists
    results.push({
      name: 'profile_debug present',
      passed: !!data.profile_debug,
      message: data.profile_debug
        ? `Found profile: ${data.profile_debug.profile_name}`
        : 'profile_debug missing from response',
      details: data.profile_debug,
    });

    // Test 2: Check profile_debug has required fields
    if (data.profile_debug) {
      const requiredFields = [
        'profile_name',
        'profile_version',
        'effective_policy',
        'recency_window_used',
        'candidates_total',
        'candidates_filtered',
        'candidates_kept',
      ];
      const missingFields = requiredFields.filter(f => !(f in data.profile_debug));
      results.push({
        name: 'profile_debug has all fields',
        passed: missingFields.length === 0,
        message: missingFields.length === 0
          ? 'All required fields present'
          : `Missing fields: ${missingFields.join(', ')}`,
        details: { missingFields },
      });

      // Test 3: Check effective_policy is present
      results.push({
        name: 'effective_policy is set',
        passed: ['public', 'internal_ok'].includes(data.profile_debug.effective_policy),
        message: `effective_policy = ${data.profile_debug.effective_policy}`,
      });

      // Test 4: Check recency_window_used
      results.push({
        name: 'recency_window_used is set',
        passed: typeof data.profile_debug.recency_window_used === 'number',
        message: `recency_window_used = ${data.profile_debug.recency_window_used} months`,
      });
    }

    // Test 5: Check answerable field exists
    results.push({
      name: 'answerable field present',
      passed: typeof data.answerable === 'boolean',
      message: `answerable = ${data.answerable}`,
    });

    // Test 6: Check answerability_reason field exists
    results.push({
      name: 'answerability_reason field present',
      passed: typeof data.answerability_reason === 'string',
      message: `answerability_reason = ${data.answerability_reason}`,
    });

    // Test 6.5: Graph context should be omitted for person_activity unless explicitly requested
    results.push({
      name: 'graph_context omitted for person_activity',
      passed: data.graph_context === undefined || data.graph_context === null,
      message: (data.graph_context === undefined || data.graph_context === null)
        ? 'graph_context not present (correct)'
        : 'graph_context present (should be omitted for person_activity)',
      details: data.graph_context ? { has_graph_context: true } : undefined,
    });

    // Test 7: Check that koi-sensors is NOT in citations
    const citations = envelope.citations || [];
    const koiSensorsCitations = citations.filter((c: any) =>
      (c.url && c.url.includes('github.com/gaiaaiagent/koi-sensors')) ||
      (c.rid && c.rid.includes('koi-sensors'))
    );
    results.push({
      name: 'koi-sensors excluded from citations',
      passed: koiSensorsCitations.length === 0,
      message: koiSensorsCitations.length === 0
        ? 'No koi-sensors citations found (correct)'
        : `Found ${koiSensorsCitations.length} koi-sensors citations (should be 0)`,
      details: koiSensorsCitations.length > 0 ? { koiSensorsCitations } : undefined,
    });

    // Test 7.5: If not answerable, results/citations should be empty for public person_activity
    if (data.answerable === false) {
      results.push({
        name: 'no results when not answerable (person_activity public)',
        passed: Array.isArray(data.results) && data.results.length === 0,
        message: Array.isArray(data.results)
          ? `results.length = ${data.results.length}`
          : 'results is not an array',
      });
      results.push({
        name: 'no citations when not answerable (person_activity public)',
        passed: Array.isArray(citations) && citations.length === 0,
        message: `citations.length = ${citations.length}`,
      });
    }

    // Test 7.6: If answerable=true, require at least one result and one citation
    if (data.answerable === true) {
      results.push({
        name: 'has results when answerable (person_activity)',
        passed: Array.isArray(data.results) && data.results.length > 0,
        message: Array.isArray(data.results)
          ? `results.length = ${data.results.length}`
          : 'results is not an array',
      });
      results.push({
        name: 'has citations when answerable (person_activity)',
        passed: Array.isArray(citations) && citations.length > 0,
        message: `citations.length = ${citations.length}`,
      });

      const forumCitations = citations.filter((c: any) => typeof c.url === 'string' && c.url.includes('forum.regen.network'));
      results.push({
        name: 'includes forum evidence when answerable (person_activity)',
        passed: forumCitations.length > 0,
        message: `forum citations = ${forumCitations.length}`,
        details: forumCitations.length > 0 ? { sample: forumCitations.slice(0, 2) } : undefined,
      });
    }

    // Test 8: If answerable=false, answerability_reason should not be 'sufficient_evidence'
    if (data.answerable === false) {
      results.push({
        name: 'answerability_reason correct when not answerable',
        passed: data.answerability_reason !== 'sufficient_evidence',
        message: `answerable=false, reason=${data.answerability_reason}`,
      });
    }

    // Test 9: Check profile_name matches expected
    if (data.profile_debug) {
      const expectedProfile = data.profile_debug.effective_policy === 'public'
        ? 'person_activity_public_v1'
        : 'person_activity_internal_v1';
      results.push({
        name: 'profile_name matches intent',
        passed: data.profile_debug.profile_name.startsWith('person_activity'),
        message: `profile_name = ${data.profile_debug.profile_name}`,
      });
    }

  } catch (error) {
    results.push({
      name: 'API Request',
      passed: false,
      message: `Request failed: ${error instanceof Error ? error.message : 'Unknown error'}`,
    });
  }

  return results;
}

async function testGeneralQueryNoProfile(): Promise<TestResult[]> {
  const results: TestResult[] = [];
  const endpoint = `${apiUrl}/api/koi/query`;

  console.log(`\n📍 Testing: POST ${endpoint}`);
  console.log(`   Query: "What is Regen Registry?"`);
  console.log(`   Intent: general (default)\n`);

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: 'What is Regen Registry?',
        limit: 5,
        // No intent specified - should default to general
      }),
    });

    if (!response.ok) {
      results.push({
        name: 'API Response Status',
        passed: false,
        message: `HTTP ${response.status}: ${response.statusText}`,
      });
      return results;
    }

    const envelope = await response.json();
    const data = envelope.data;

    // Test: General intent should still have profile_debug
    results.push({
      name: 'profile_debug present for general intent',
      passed: !!data.profile_debug,
      message: data.profile_debug
        ? `profile_name = ${data.profile_debug.profile_name}`
        : 'profile_debug missing',
    });

    // Test: General intent should have answerable=true (no filtering)
    results.push({
      name: 'general intent is answerable',
      passed: data.answerable === true,
      message: `answerable = ${data.answerable}`,
    });

    // Test: General intent profile should be general_v1
    if (data.profile_debug) {
      results.push({
        name: 'general intent uses general_v1 profile',
        passed: data.profile_debug.profile_name === 'general_v1',
        message: `profile_name = ${data.profile_debug.profile_name}`,
      });
    }

  } catch (error) {
    results.push({
      name: 'API Request',
      passed: false,
      message: `Request failed: ${error instanceof Error ? error.message : 'Unknown error'}`,
    });
  }

  return results;
}

// =============================================================================
// Main
// =============================================================================

async function main() {
  console.log('='.repeat(60));
  console.log('Intent-Aware Retrieval Profile Regression Test');
  console.log('Session KOI: "mention ≠ evidence" MVP');
  console.log('='.repeat(60));
  console.log(`\nAPI URL: ${apiUrl}`);

  const allResults: TestResult[] = [];

  // Run test suites
  const personActivityResults = await testPersonActivityQuery();
  allResults.push(...personActivityResults);
  personActivityResults.forEach(logResult);

  const generalResults = await testGeneralQueryNoProfile();
  allResults.push(...generalResults);
  generalResults.forEach(logResult);

  // Summary
  const passed = allResults.filter(r => r.passed).length;
  const failed = allResults.filter(r => !r.passed).length;

  console.log('\n' + '='.repeat(60));
  console.log(`Summary: ${passed} passed, ${failed} failed`);
  console.log('='.repeat(60));

  if (failed > 0) {
    console.log('\n❌ Some tests failed. Please review the output above.');
    process.exit(1);
  } else {
    console.log('\n✅ All tests passed!');
    process.exit(0);
  }
}

main().catch(error => {
  console.error('Fatal error:', error);
  process.exit(1);
});
