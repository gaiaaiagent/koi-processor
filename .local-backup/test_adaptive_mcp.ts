#!/usr/bin/env bun
/**
 * Test script for Adaptive Knowledge MCP Server
 * Tests RRF, confidence monitoring, and query logging with production data
 */

import { Pool } from "pg";

// Import the adaptive features we want to test
import {
  reciprocalRankFusion,
  calculateConfidence,
  logQuery,
  shouldTriggerExtraction,
  selectDocumentsForExtraction
} from "./adaptive-features.js";

// Database configuration
const POSTGRES_URL = process.env.POSTGRES_URL || "postgresql://postgres:postgres@localhost:5433/eliza";

// Parse PostgreSQL connection string
function parsePostgresUrl(url: string) {
  const match = url.match(/postgres(?:ql)?:\/\/([^:]+):([^@]+)@([^:]+):(\d+)\/(.+)/);
  if (!match) throw new Error("Invalid PostgreSQL URL");
  return {
    user: match[1],
    password: match[2],
    host: match[3],
    port: parseInt(match[4]),
    database: match[5]
  };
}

// Initialize database connection
const dbConfig = parsePostgresUrl(POSTGRES_URL);
const pool = new Pool(dbConfig);

// Sample test queries for regenerative agriculture
const TEST_QUERIES = [
  "What is regenerative agriculture?",
  "How does carbon sequestration work in soil?",
  "What are the benefits of biochar for carbon storage?",
  "How do regenerative practices improve soil health?",
  "What is the role of mycorrhizal fungi in carbon cycling?",
  "How can farmers transition to regenerative practices?",
  "What are the economic benefits of regenerative agriculture?",
  "How does cover cropping help with soil carbon?",
  "What is holistic management in grazing systems?",
  "How do we measure carbon credits in agriculture?"
];

// Mock search function to simulate semantic search results
async function mockSemanticSearch(query: string, topK: number = 10) {
  // Get real results from our production data
  const searchQuery = `
    SELECT 
      m.rid,
      m.content->>'text' as content,
      m.metadata->>'source' as source,
      e.dim_1024 <=> $2 as distance,
      1 - (e.dim_1024 <=> $2) as similarity
    FROM koi_memories m
    JOIN koi_embeddings e ON e.memory_id = m.id
    WHERE 
      e.dim_1024 IS NOT NULL
      AND m.content->>'text' IS NOT NULL
      AND LENGTH(m.content->>'text') > 50
      AND to_tsvector('english', m.content->>'text') @@ plainto_tsquery('english', $1)
    ORDER BY e.dim_1024 <=> $2
    LIMIT $3
  `;

  // Use a random embedding as query embedding (in real system this would be generated)
  const randomEmbedding = await pool.query(
    "SELECT dim_1024 FROM koi_embeddings WHERE dim_1024 IS NOT NULL ORDER BY RANDOM() LIMIT 1"
  );

  if (randomEmbedding.rows.length === 0) {
    return [];
  }

  const results = await pool.query(searchQuery, [query, randomEmbedding.rows[0].dim_1024, topK]);
  
  return results.rows.map(row => ({
    id: row.rid,
    content: row.content.substring(0, 200) + "...",
    similarity: parseFloat(row.similarity),
    score: parseFloat(row.similarity),
    source: 'vector' as const,
    metadata: {
      rid: row.rid,
      source: row.source,
      similarity: parseFloat(row.similarity)
    },
    rid: row.rid
  }));
}

// Mock SPARQL search (simplified)
async function mockSparqlSearch(query: string, topK: number = 10) {
  // Simple keyword-based fallback for SPARQL results
  const sparqlQuery = `
    SELECT 
      m.rid,
      m.content->>'text' as content,
      m.metadata->>'source' as source
    FROM koi_memories m
    WHERE 
      m.content->>'text' ILIKE $1
      AND LENGTH(m.content->>'text') > 50
    ORDER BY RANDOM()
    LIMIT $2
  `;

  const results = await pool.query(sparqlQuery, [`%${query}%`, topK]);
  
  return results.rows.map((row, index) => ({
    id: row.rid,
    content: row.content.substring(0, 200) + "...",
    similarity: 1.0 - (index * 0.1), // Decreasing score
    score: 1.0 - (index * 0.1),
    source: 'sparql' as const,
    metadata: {
      rid: row.rid,
      source: row.source
    },
    rid: row.rid
  }));
}

// Test RRF with real data
async function testRRFWithRealData() {
  console.log("\n🔍 Testing Reciprocal Rank Fusion with Production Data");
  console.log("=" .repeat(60));

  for (const query of TEST_QUERIES.slice(0, 5)) { // Test first 5 queries
    console.log(`\nQuery: "${query}"`);
    
    try {
      // Get semantic and SPARQL results
      const vectorResults = await mockSemanticSearch(query, 8);
      const sparqlResults = await mockSparqlSearch(query, 5);
      
      console.log(`  Vector results: ${vectorResults.length}`);
      console.log(`  SPARQL results: ${sparqlResults.length}`);
      
      if (vectorResults.length === 0 && sparqlResults.length === 0) {
        console.log("  ⚠️ No results found for this query");
        continue;
      }
      
      // Apply RRF
      const fusedResults = reciprocalRankFusion(vectorResults, sparqlResults);
      console.log(`  RRF fused results: ${fusedResults.length}`);
      
      // Calculate confidence
      const confidence = calculateConfidence(fusedResults);
      console.log(`  Confidence: ${confidence.toFixed(3)}`);
      
      // Check if extraction should be triggered
      const shouldExtract = shouldTriggerExtraction(confidence);
      console.log(`  Trigger extraction: ${shouldExtract ? 'YES' : 'NO'}`);
      
      if (shouldExtract) {
        const selectedDocs = selectDocumentsForExtraction(fusedResults, 3);
        console.log(`  Documents selected for extraction: ${selectedDocs.length}`);
      }
      
      // Log the query
      await logQuery(pool, {
        query_text: query,
        user_id: "00000000-0000-0000-0000-000000000001",
        agent_id: "00000000-0000-0000-0000-000000000002",
        confidence_score: confidence,
        triggered_extraction: shouldExtract,
        response_time_ms: 150,
        results: fusedResults.slice(0, 5)
      });
      
      console.log(`  ✅ Query logged to database`);
      
    } catch (error) {
      console.error(`  ❌ Error processing query: ${error}`);
    }
  }
}

// Test confidence score distribution
async function testConfidenceDistribution() {
  console.log("\n📊 Testing Confidence Score Distribution");
  console.log("=" .repeat(60));
  
  const confidenceScores: number[] = [];
  
  for (const query of TEST_QUERIES) {
    try {
      const vectorResults = await mockSemanticSearch(query, 10);
      const sparqlResults = await mockSparqlSearch(query, 5);
      
      if (vectorResults.length > 0 || sparqlResults.length > 0) {
        const fusedResults = reciprocalRankFusion(vectorResults, sparqlResults);
        const confidence = calculateConfidence(fusedResults);
        confidenceScores.push(confidence);
        
        console.log(`Query: "${query.substring(0, 40)}..." -> Confidence: ${confidence.toFixed(3)}`);
      }
    } catch (error) {
      console.error(`Error with query "${query}": ${error}`);
    }
  }
  
  if (confidenceScores.length > 0) {
    const avgConfidence = confidenceScores.reduce((a, b) => a + b, 0) / confidenceScores.length;
    const minConfidence = Math.min(...confidenceScores);
    const maxConfidence = Math.max(...confidenceScores);
    const lowConfidenceCount = confidenceScores.filter(c => c < 0.7).length;
    
    console.log(`\nConfidence Statistics:`);
    console.log(`  Average: ${avgConfidence.toFixed(3)}`);
    console.log(`  Range: ${minConfidence.toFixed(3)} - ${maxConfidence.toFixed(3)}`);
    console.log(`  Low confidence (< 0.7): ${lowConfidenceCount}/${confidenceScores.length} (${(lowConfidenceCount/confidenceScores.length*100).toFixed(1)}%)`);
  }
}

// Test query logging and analytics
async function testQueryAnalytics() {
  console.log("\n📈 Testing Query Analytics");
  console.log("=" .repeat(60));
  
  // Check recent queries
  const recentQueries = await pool.query(`
    SELECT 
      query_text,
      confidence_score,
      triggered_extraction,
      response_time_ms,
      timestamp
    FROM koi_query_log
    WHERE timestamp > NOW() - INTERVAL '1 hour'
    ORDER BY timestamp DESC
    LIMIT 10
  `);
  
  console.log(`Recent queries (last hour): ${recentQueries.rows.length}`);
  
  if (recentQueries.rows.length > 0) {
    console.log("\nLatest queries:");
    recentQueries.rows.forEach(row => {
      console.log(`  "${row.query_text.substring(0, 40)}..." | Confidence: ${row.confidence_score?.toFixed(3) || 'N/A'} | Extraction: ${row.triggered_extraction}`);
    });
  }
  
  // Check problematic queries
  const problematicQueries = await pool.query(`
    SELECT * FROM koi_problematic_queries
    LIMIT 5
  `);
  
  console.log(`\nProblematic queries (low confidence, high frequency): ${problematicQueries.rows.length}`);
  problematicQueries.rows.forEach(row => {
    console.log(`  "${row.query_text.substring(0, 40)}..." | Avg confidence: ${row.avg_confidence?.toFixed(3)} | Frequency: ${row.frequency}`);
  });
}

// Main test function
async function runTests() {
  console.log("🚀 Adaptive Knowledge MCP Server Test Suite");
  console.log("Testing with production data from KOI pipeline");
  
  try {
    // Check database connection
    const dbCheck = await pool.query("SELECT COUNT(*) FROM koi_memories");
    console.log(`\n✅ Database connected. Found ${dbCheck.rows[0].count} KOI memories`);
    
    const embeddingCheck = await pool.query("SELECT COUNT(*) FROM koi_embeddings WHERE dim_1024 IS NOT NULL");
    console.log(`✅ Found ${embeddingCheck.rows[0].count} BGE embeddings`);
    
    // Run tests
    await testRRFWithRealData();
    await testConfidenceDistribution();
    await testQueryAnalytics();
    
    console.log("\n🎉 All tests completed successfully!");
    console.log("\nNext steps:");
    console.log("1. Start the enhanced MCP server: ./bge-mcp-ts/run-enhanced-mcp.sh");
    console.log("2. Test with real agent queries");
    console.log("3. Monitor confidence patterns over time");
    
  } catch (error) {
    console.error("❌ Test failed:", error);
  } finally {
    await pool.end();
  }
}

// Run the tests
if (import.meta.main) {
  runTests();
}