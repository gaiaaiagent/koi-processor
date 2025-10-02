#!/usr/bin/env bun
/**
 * KOI Query API Bridge
 * Provides REST API bridge to adaptive hybrid RAG functionality
 * Serves the GAIA React interface
 */

import express from 'express';
import cors from 'cors';
import { Pool } from "pg";

// Import the adaptive features
import {
  reciprocalRankFusion,
  calculateConfidence,
  logQuery,
  shouldTriggerExtraction,
  selectDocumentsForExtraction
} from "./bge-mcp-ts/adaptive-features.js";

const app = express();
const PORT = 8301;

// Database configuration
const POSTGRES_URL = process.env.POSTGRES_URL || "postgresql://postgres:postgres@localhost:5433/eliza";

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

const dbConfig = parsePostgresUrl(POSTGRES_URL);
const pool = new Pool(dbConfig);

// HNSW index provides excellent recall without tuning parameters
// No need to set probes or other runtime parameters

// Middleware
app.use(cors());
app.use(express.json());

// Semantic search using BGE embeddings 
async function performSemanticSearch(query: string, topK: number = 10) {
  try {
    // Generate embedding for the query using BGE API
    const bgeResponse = await fetch('http://localhost:8090/encode', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ text: query })
    });

    if (!bgeResponse.ok) {
      console.log('BGE API not available, falling back to ILIKE search');
      // Fallback to simple text search when BGE API is not available
      const fallbackQuery = `
        SELECT 
          m.rid,
          m.content->>'text' as content,
          m.metadata->>'source' as source,
          0.5 as similarity
        FROM koi_memories m
        WHERE 
          m.content->>'text' IS NOT NULL
          AND LENGTH(m.content->>'text') > 50
          AND (
            m.content->>'text' ILIKE $1
            OR m.content->>'text' ILIKE $2
          )
        ORDER BY RANDOM()
        LIMIT $3
      `;
      
      const results = await pool.query(fallbackQuery, [`%${query}%`, `%${query.replace(/\s+/g, '%')}%`, topK]);
      
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

    const bgeData = await bgeResponse.json();
    const queryEmbedding = bgeData.embedding;

    // Convert array to PostgreSQL vector format
    const vectorString = `[${queryEmbedding.join(',')}]`;

    const searchQuery = `
      SELECT 
        m.rid,
        m.content->>'text' as content,
        m.metadata->>'source' as source,
        e.dim_1024 <=> $1::vector as distance,
        1 - (e.dim_1024 <=> $1::vector) as similarity
      FROM koi_memories m
      JOIN koi_embeddings e ON e.memory_id = m.id
      WHERE 
        e.dim_1024 IS NOT NULL
        AND m.content->>'text' IS NOT NULL
        AND LENGTH(m.content->>'text') > 50
      ORDER BY e.dim_1024 <=> $1::vector
      LIMIT $2
    `;

    const results = await pool.query(searchQuery, [vectorString, topK]);
    
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
  } catch (error) {
    console.error('Semantic search error:', error);
    return [];
  }
}

// Trigger adaptive extraction using Python extractor
async function triggerAdaptiveExtraction(
  query: string, 
  searchResults: any[], 
  userId: string = 'web-user', 
  agentId: string = 'koi-interface'
) {
  try {
    const extractionPayload = {
      query,
      search_results: searchResults,
      user_id: userId,
      agent_id: agentId
    };

    // Call Python adaptive extraction service
    const response = await fetch('http://localhost:8350/extract', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(extractionPayload)
    });

    if (!response.ok) {
      throw new Error(`Extraction service error: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Failed to trigger adaptive extraction:', error);
    throw error;
  }
}


// Hybrid RAG Query Endpoint
// Full-text search using PostgreSQL's ts_rank (BM25-like ranking)
async function performKeywordSearch(query: string, topK: number = 10) {
  try {
    // Split query into words and clean them
    const words = query.trim().split(/\s+/)
      .map(word => word.replace(/[^a-zA-Z0-9]/g, ''))
      .filter(w => w.length > 0);

    if (words.length === 0) {
      return [];
    }

    // Create both AND and OR queries for better name matching
    // For names like "greg landua", we want to match "Gregory Landua" too
    const andQuery = words.join(' & ');
    const orQuery = words.join(' | ');

    // Use prefix matching with :* for partial name matches
    const prefixQuery = words.map(w => `${w}:*`).join(' | ');

    // Try AND first, then fall back to OR with prefix matching
    const searchQuery = `
      SELECT
        m.rid,
        m.content->>'text' as content,
        m.metadata->>'source' as source,
        m.metadata->>'url' as url,
        ts_rank_cd(m.content_tsv, to_tsquery('english', $1)) as rank,
        'strict' as match_type
      FROM koi_memories m
      WHERE
        m.content_tsv @@ to_tsquery('english', $1)
        AND m.content->>'text' IS NOT NULL
        AND LENGTH(m.content->>'text') > 50

      UNION ALL

      SELECT
        m.rid,
        m.content->>'text' as content,
        m.metadata->>'source' as source,
        m.metadata->>'url' as url,
        ts_rank_cd(m.content_tsv, to_tsquery('english', $2)) as rank,
        'relaxed' as match_type
      FROM koi_memories m
      WHERE
        m.content_tsv @@ to_tsquery('english', $2)
        AND m.content->>'text' IS NOT NULL
        AND LENGTH(m.content->>'text') > 50
        AND NOT EXISTS (
          SELECT 1 FROM koi_memories m2
          WHERE m2.rid = m.rid
          AND m2.content_tsv @@ to_tsquery('english', $1)
        )

      ORDER BY rank DESC
      LIMIT $3
    `;

    // Increased multiplier to ensure OR results are well represented
    // With AND getting ~10 results and OR getting ~10, we need more total results
    const results = await pool.query(searchQuery, [andQuery, orQuery, Math.max(topK * 3, 50)]);

    // Find max rank for normalization
    const maxRank = results.rows.length > 0
      ? Math.max(...results.rows.map(r => parseFloat(r.rank)))
      : 1;

    return results.rows.slice(0, topK).map(row => {
      const rawRank = parseFloat(row.rank);

      // Logarithmic scaling for better score discrimination
      const normalizedScore = Math.log(1 + rawRank) / Math.log(1 + maxRank);

      // Boost for exact phrase matches (case-insensitive)
      const queryLower = query.toLowerCase();
      const contentLower = row.content.toLowerCase();
      const hasExactMatch = contentLower.includes(queryLower);

      // Boost strict matches (AND query) over relaxed matches (OR query)
      const matchTypeBoost = row.match_type === 'strict' ? 1.0 : 0.9;
      const finalScore = (hasExactMatch ? normalizedScore * 1.2 : normalizedScore) * matchTypeBoost;

      return {
        id: row.rid,
        content: row.content.substring(0, 200) + "...",
        similarity: finalScore,
        score: finalScore,
        source: 'keyword' as const,
        metadata: {
          rid: row.rid,
          source: row.source,
          url: row.url,
          fts_rank: rawRank,
          normalized_score: normalizedScore,
          exact_match_boost: hasExactMatch,
          match_type: row.match_type
        },
        rid: row.rid
      };
    });
  } catch (error) {
    console.error('[Keyword Search] Error:', error);
    // Fallback to ILIKE if FTS fails
    const fallbackQuery = `
      SELECT
        m.rid,
        m.content->>'text' as content,
        m.metadata->>'source' as source,
        m.metadata->>'url' as url,
        0.5 as rank
      FROM koi_memories m
      WHERE
        m.content->>'text' ILIKE $1
        AND LENGTH(m.content->>'text') > 50
      ORDER BY CASE
        WHEN m.content->>'text' ILIKE $2 THEN 3  -- Exact phrase match
        WHEN m.content->>'text' ILIKE $1 THEN 2  -- Contains all words
        ELSE 1
      END DESC
      LIMIT $3
    `;

    const results = await pool.query(fallbackQuery, [`%${query}%`, `%${query}%`, topK]);

    return results.rows.map(row => ({
      id: row.rid,
      content: row.content.substring(0, 200) + "...",
      similarity: parseFloat(row.rank),
      score: parseFloat(row.rank),
      source: 'keyword' as const,
      metadata: {
        rid: row.rid,
        source: row.source,
        url: row.url
      },
      rid: row.rid
    }));
  }
}
app.post('/api/koi/query', async (req, res) => {
  try {
    const { question, user_id = 'web-user', agent_id = 'koi-interface', limit = 10 } = req.body;

    if (!question) {
      return res.status(400).json({ error: 'Question is required' });
    }

    const startTime = Date.now();

    // Perform hybrid search with RRF
    // Increased limits to capture more diverse results before fusion
    // Higher keyword limit to ensure important biographical pages (rank ~#11) are included
    // Increased to 20 to capture team/profile pages that rank just outside top-15
    const [vectorResults, keywordResults] = await Promise.all([
      performSemanticSearch(question, 20),
      performKeywordSearch(question, 20)
    ]);

    // Apply Reciprocal Rank Fusion
    // Pass empty array for SPARQL results (not implemented yet)
    const fusedResults = reciprocalRankFusion(vectorResults, [], keywordResults);
    
    // Calculate confidence
    const confidence = calculateConfidence(fusedResults);
    
    // Check if extraction should be triggered
    const triggeredExtraction = shouldTriggerExtraction(confidence);
    
    // Select documents for extraction if needed
    let selectedDocs = [];
    let extractionResult = null;
    if (triggeredExtraction) {
      selectedDocs = selectDocumentsForExtraction(fusedResults, 3);
      
      // Trigger Python adaptive extraction
      try {
        console.log(`🔧 Triggering adaptive extraction for query: "${question}" (confidence: ${confidence.toFixed(3)})`);
        extractionResult = await triggerAdaptiveExtraction(question, fusedResults, user_id, agent_id);
        console.log(`✅ Extraction completed with ${extractionResult?.extracted_facts?.length || 0} facts extracted`);
      } catch (error) {
        console.error('❌ Adaptive extraction failed:', error);
        // Continue without failing the main query
      }
    }

    const responseTime = Date.now() - startTime;

    // Log the query
    await logQuery(pool, {
      query_text: question,
      user_id,
      agent_id,
      confidence_score: confidence,
      triggered_extraction: triggeredExtraction,
      response_time_ms: responseTime,
      results: fusedResults.slice(0, 5)
    });

    // Format response
    const response = {
      question,
      total_results: fusedResults.length,
      confidence: confidence,
      execution_time: responseTime / 1000,
      triggered_extraction: triggeredExtraction,
      results: fusedResults.slice(0, limit).map(r => ({
        title: `Document ${r.rid}`,
        content: r.content,
        score: r.score,
        source: r.source,
        rid: r.rid
      }))
    };

    res.json(response);

  } catch (error) {
    console.error('Query error:', error);
    res.status(500).json({ 
      error: 'Internal server error',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Proxy middleware for KOI services
const proxyToService = (serviceUrl: string) => {
  return async (req: express.Request, res: express.Response) => {
    try {
      const targetUrl = `${serviceUrl}${req.originalUrl}`;
      const response = await fetch(targetUrl, {
        method: req.method,
        headers: {
          'Content-Type': 'application/json',
          ...req.headers
        },
        body: req.method !== 'GET' ? JSON.stringify(req.body) : undefined
      });

      const data = await response.text();
      res.status(response.status);
      
      // Set content type based on response
      const contentType = response.headers.get('content-type');
      if (contentType) {
        res.set('Content-Type', contentType);
      }
      
      res.send(data);
    } catch (error) {
      console.error(`Proxy error for ${req.originalUrl}:`, error);
      res.status(503).json({ 
        error: 'Service unavailable',
        service: serviceUrl,
        message: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  };
};

// KOI Service Proxies (Development API Gateway)
app.use('/api/koi/graph/', proxyToService('http://localhost:8002'));
app.use('/api/koi/coordinator/', proxyToService('http://localhost:8005'));
app.use('/api/koi/event-bridge/', proxyToService('http://localhost:8100'));
app.use('/api/koi/bge/', proxyToService('http://localhost:8090'));
app.use('/api/koi/transformations', proxyToService('http://localhost:8002'));
app.use('/api/koi/rids', proxyToService('http://localhost:8002'));

// Health check
app.get('/api/koi/health', async (req, res) => {
  try {
    const dbCheck = await pool.query("SELECT COUNT(*) FROM koi_memories");
    const embeddingCheck = await pool.query("SELECT COUNT(*) FROM koi_embeddings WHERE dim_1024 IS NOT NULL");
    
    res.json({
      status: 'healthy',
      database: 'connected',
      memories: dbCheck.rows[0].count,
      embeddings: embeddingCheck.rows[0].count,
      timestamp: new Date().toISOString()
    });
  } catch (error) {
    res.status(500).json({
      status: 'unhealthy',
      error: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Start server
app.listen(PORT, () => {
  console.log(`🚀 KOI Query API running on http://localhost:${PORT}`);
  console.log(`📊 Health check: http://localhost:${PORT}/api/koi/health`);
  console.log(`🔍 Query endpoint: POST http://localhost:${PORT}/api/koi/query`);
});

export default app;