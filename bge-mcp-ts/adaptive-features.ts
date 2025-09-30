/**
 * Adaptive Knowledge Features for MCP Server
 * Implements RRF, confidence monitoring, and query logging
 */

import { Pool } from "pg";

// Interfaces
interface SearchResult {
  id: string;
  content: string;
  similarity?: number;
  score?: number;
  source: 'vector' | 'sparql' | 'keyword' | 'hybrid';
  metadata?: any;
  rid?: string;
}

interface QueryLogEntry {
  query_text: string;
  query_embedding?: number[];
  user_id?: string;
  agent_id?: string;
  confidence_score?: number;
  triggered_extraction?: boolean;
  extraction_receipt_rid?: string;
  response_time_ms?: number;
  results?: SearchResult[];
}

/**
 * Reciprocal Rank Fusion (RRF) Implementation
 * Combines multiple ranked lists into a single ranking
 * Based on research showing 20-30% improvement in retrieval
 */
export function reciprocalRankFusion(
  vectorResults: SearchResult[],
  sparqlResults: SearchResult[],
  keywordResults?: SearchResult[]
): SearchResult[] {
  const k = 60; // Standard constant from RRF research
  const fusedScores = new Map<string, number>();
  const resultMetadata = new Map<string, SearchResult>();

  // Process each result set
  const resultSets = [
    { results: vectorResults, source: 'vector' },
    { results: sparqlResults, source: 'sparql' },
    ...(keywordResults ? [{ results: keywordResults, source: 'keyword' }] : [])
  ];

  resultSets.forEach(({ results, source }) => {
    results.forEach((result, rank) => {
      const id = result.rid || result.id;
      const currentScore = fusedScores.get(id) || 0;
      const rrfScore = 1 / (k + rank + 1);
      
      fusedScores.set(id, currentScore + rrfScore);

      // Preserve metadata from highest scoring source
      if (!resultMetadata.has(id) || rrfScore > currentScore) {
        resultMetadata.set(id, { ...result, source: source as any });
      }
    });
  });

  // Sort by fused score
  return Array.from(fusedScores.entries())
    .map(([id, score]) => ({
      ...resultMetadata.get(id)!,
      id,
      score,
      source: 'hybrid' as const
    }))
    .sort((a, b) => (b.score || 0) - (a.score || 0));
}

/**
 * Calculate confidence score for retrieval results
 * Used to trigger adaptive extraction when confidence is low
 */
export function calculateConfidence(results: SearchResult[]): number {
  if (!results || results.length === 0) return 0;

  // Multiple factors for confidence calculation
  const factors = {
    // Top result similarity score
    topScore: results[0]?.similarity || results[0]?.score || 0,
    
    // Gap between top results (larger gap = higher confidence)
    scoreGap: results.length > 1 
      ? (results[0]?.similarity || results[0]?.score || 0) - 
        (results[1]?.similarity || results[1]?.score || 0)
      : 0.5,
    
    // Number of results (normalized)
    resultCount: Math.min(results.length / 10, 1),
    
    // Average score of top 5 results
    averageScore: results
      .slice(0, 5)
      .reduce((sum, r) => sum + (r.similarity || r.score || 0), 0) / 
      Math.min(5, results.length),
    
    // Score distribution variance (lower variance = higher confidence)
    scoreVariance: calculateScoreVariance(results.slice(0, 10))
  };

  // Weighted confidence score
  const confidence = 
    factors.topScore * 0.35 +
    factors.scoreGap * 0.20 +
    factors.resultCount * 0.15 +
    factors.averageScore * 0.20 +
    (1 - factors.scoreVariance) * 0.10;

  return Math.min(1, Math.max(0, confidence));
}

/**
 * Calculate score variance for confidence assessment
 */
function calculateScoreVariance(results: SearchResult[]): number {
  if (results.length < 2) return 0;
  
  const scores = results.map(r => r.similarity || r.score || 0);
  const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
  const variance = scores.reduce((sum, score) => 
    sum + Math.pow(score - mean, 2), 0) / scores.length;
  
  // Normalize to 0-1 range
  return Math.min(1, variance);
}

/**
 * Log query for analysis and learning
 */
export async function logQuery(
  pool: Pool,
  entry: QueryLogEntry
): Promise<void> {
  try {
    const query = `
      INSERT INTO koi_query_log (
        query_text,
        query_embedding,
        user_id,
        agent_id,
        confidence_score,
        triggered_extraction,
        extraction_receipt_rid,
        response_time_ms
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      RETURNING id
    `;

    const embedding = entry.query_embedding 
      ? `[${entry.query_embedding.join(',')}]` 
      : null;

    await pool.query(query, [
      entry.query_text,
      embedding,
      entry.user_id || null,
      entry.agent_id || null,
      entry.confidence_score || null,
      entry.triggered_extraction || false,
      entry.extraction_receipt_rid || null,
      entry.response_time_ms || null
    ]);
  } catch (error) {
    console.error("[Query Log] Failed to log query:", error);
    // Don't throw - logging should not break the main flow
  }
}

/**
 * Get query patterns for active learning
 */
export async function getQueryPatterns(
  pool: Pool,
  minConfidence: number = 0.7
): Promise<any[]> {
  try {
    const query = `
      SELECT 
        query_text,
        COUNT(*) as frequency,
        AVG(confidence_score) as avg_confidence,
        COUNT(CASE WHEN triggered_extraction THEN 1 END) as extraction_count,
        MIN(confidence_score) as min_confidence,
        MAX(confidence_score) as max_confidence
      FROM koi_query_log
      WHERE timestamp > NOW() - INTERVAL '7 days'
      GROUP BY query_text
      HAVING AVG(confidence_score) < $1
      ORDER BY frequency DESC, avg_confidence ASC
      LIMIT 100
    `;

    const result = await pool.query(query, [minConfidence]);
    return result.rows;
  } catch (error) {
    console.error("[Query Patterns] Failed to get patterns:", error);
    return [];
  }
}

/**
 * Check if extraction should be triggered based on confidence
 */
export function shouldTriggerExtraction(
  confidence: number,
  threshold: number = 0.7
): boolean {
  return confidence < threshold;
}

/**
 * Calculate IDDS score for active learning document selection
 * Balances informativeness (representativeness) and diversity
 */
export function calculateIDDSScore(
  doc: SearchResult,
  unlabeledPool: SearchResult[],
  selectedPool: SearchResult[],
  alpha: number = 0.5
): number {
  // Informativeness: How similar to other unlabeled docs (representative)
  const informativeness = unlabeledPool
    .filter(d => d.id !== doc.id)
    .reduce((sum, other) => {
      const similarity = cosineSimilarity(
        doc.metadata?.embedding || [],
        other.metadata?.embedding || []
      );
      return sum + similarity;
    }, 0) / Math.max(1, unlabeledPool.length - 1);

  // Diversity: How different from already processed docs (novel)
  let diversity = 1.0;
  if (selectedPool.length > 0) {
    const maxSimilarity = selectedPool.reduce((max, selected) => {
      const similarity = cosineSimilarity(
        doc.metadata?.embedding || [],
        selected.metadata?.embedding || []
      );
      return Math.max(max, similarity);
    }, 0);
    diversity = 1 - maxSimilarity;
  }

  // Combined score with balance parameter
  return alpha * informativeness + (1 - alpha) * diversity;
}

/**
 * Select documents for extraction using active learning
 */
export function selectDocumentsForExtraction(
  retrievedDocs: SearchResult[],
  budget: number = 5,
  alpha: number = 0.5
): SearchResult[] {
  const selected: SearchResult[] = [];
  const unlabeled = [...retrievedDocs];

  for (let i = 0; i < Math.min(budget, retrievedDocs.length); i++) {
    // Calculate IDDS for remaining documents
    const scores = unlabeled.map(doc => ({
      doc,
      score: calculateIDDSScore(doc, unlabeled, selected, alpha)
    }));

    // Select highest scoring document
    const best = scores.reduce((max, current) => 
      current.score > max.score ? current : max
    );

    selected.push(best.doc);
    const index = unlabeled.indexOf(best.doc);
    if (index > -1) {
      unlabeled.splice(index, 1);
    }
  }

  return selected;
}

/**
 * Simple cosine similarity for embeddings
 */
function cosineSimilarity(a: number[], b: number[]): number {
  if (!a.length || !b.length || a.length !== b.length) return 0;
  
  let dotProduct = 0;
  let normA = 0;
  let normB = 0;
  
  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  
  const denominator = Math.sqrt(normA) * Math.sqrt(normB);
  return denominator === 0 ? 0 : dotProduct / denominator;
}

/**
 * Create database schema for query logging
 */
export const CREATE_QUERY_LOG_SCHEMA = `
CREATE TABLE IF NOT EXISTS koi_query_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    query_embedding vector(1024),
    user_id UUID,
    agent_id UUID,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    confidence_score FLOAT,
    triggered_extraction BOOLEAN DEFAULT FALSE,
    extraction_receipt_rid TEXT,
    response_time_ms INTEGER,
    feedback_provided BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_query_confidence ON koi_query_log(confidence_score);
CREATE INDEX IF NOT EXISTS idx_query_timestamp ON koi_query_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_query_extraction ON koi_query_log(triggered_extraction) 
  WHERE triggered_extraction = TRUE;
CREATE INDEX IF NOT EXISTS idx_query_feedback ON koi_query_log(feedback_provided) 
  WHERE feedback_provided = FALSE;
`;

// Export all functions
export default {
  reciprocalRankFusion,
  calculateConfidence,
  logQuery,
  getQueryPatterns,
  shouldTriggerExtraction,
  calculateIDDSScore,
  selectDocumentsForExtraction,
  CREATE_QUERY_LOG_SCHEMA
};