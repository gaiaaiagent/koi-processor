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
async function performSemanticSearch(query: string, topK: number = 10, filters?: any) {
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
      const dateClauses: string[] = [];
      const params: any[] = [`%${query}%`, `%${query.replace(/\s+/g, '%')}%`];
      if (filters?.date_range?.start) {
        dateClauses.push(`m.published_at >= $${params.length + 1}::timestamptz`);
        params.push(filters.date_range.start);
      }
      if (filters?.date_range?.end) {
        dateClauses.push(`m.published_at <= $${params.length + 1}::timestamptz`);
        params.push(filters.date_range.end);
      }
      const whereDate = dateClauses.length
        ? ` AND (${dateClauses.join(' AND ')}${filters?.include_undated ? ' OR m.published_at IS NULL' : ''})`
        : '';
      const fallbackQuery = `
        SELECT 
          m.rid,
          m.content->>'text' as content,
          m.metadata->>'source' as source,
          m.metadata->>'url' as url,
          0.5 as similarity,
          m.published_at
        FROM koi_memories m
        WHERE 
          m.content->>'text' IS NOT NULL
          AND LENGTH(m.content->>'text') > 50
          AND (
            m.content->>'text' ILIKE $1
            OR m.content->>'text' ILIKE $2
          )
          ${whereDate}
        ORDER BY RANDOM()
        LIMIT $${params.length + 1}
      `;
      params.push(topK);
      const results = await pool.query(fallbackQuery, params);
      
      return results.rows.map(row => ({
        id: row.rid,
        content: row.content.substring(0, 200) + "...",
        similarity: parseFloat(row.similarity),
        score: parseFloat(row.similarity),
        source: 'vector' as const,
        metadata: {
          rid: row.rid,
          source: row.source,
          url: row.url,
          similarity: parseFloat(row.similarity),
          published_at: row.published_at || null
        },
        rid: row.rid
      }));
    }

    const bgeData = await bgeResponse.json();
    const queryEmbedding = bgeData.embedding;

    // Convert array to PostgreSQL vector format
    const vectorString = `[${queryEmbedding.join(',')}]`;

    // Optional date filters
    const dateClauses: string[] = [];
    const params: any[] = [vectorString];
    if (filters?.date_range?.start) {
      dateClauses.push(`m.published_at >= $${params.length + 1}::timestamptz`);
      params.push(filters.date_range.start);
    }
    if (filters?.date_range?.end) {
      dateClauses.push(`m.published_at <= $${params.length + 1}::timestamptz`);
      params.push(filters.date_range.end);
    }
    const andDate = dateClauses.length
      ? ` AND (${dateClauses.join(' AND ')}${filters?.include_undated ? ' OR m.published_at IS NULL' : ''})`
      : '';

    const searchQuery = `
      SELECT 
        m.rid,
        m.content->>'text' as content,
        m.metadata->>'source' as source,
        m.metadata->>'url' as url,
        e.dim_1024 <=> $1::vector as distance,
        1 - (e.dim_1024 <=> $1::vector) as similarity,
        m.published_at
      FROM koi_memories m
      JOIN koi_embeddings e ON e.memory_id = m.id
      WHERE 
        e.dim_1024 IS NOT NULL
        AND m.content->>'text' IS NOT NULL
        AND LENGTH(m.content->>'text') > 50
        ${andDate}
      ORDER BY e.dim_1024 <=> $1::vector
      LIMIT $${params.length + 1}
    `;

    params.push(topK);
    const results = await pool.query(searchQuery, params);

    return results.rows.map(row => ({
      id: row.rid,
      content: row.content.substring(0, 200) + "...",
      similarity: parseFloat(row.similarity),
      score: parseFloat(row.similarity),
      source: 'vector' as const,
      metadata: {
        rid: row.rid,
        source: row.source,
        url: row.url,
        similarity: parseFloat(row.similarity),
        published_at: row.published_at || null
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
async function performKeywordSearch(query: string, topK: number = 10, filters?: any) {
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
    const dateClauses: string[] = [];
    const params: any[] = [andQuery, orQuery];
    if (filters?.date_range?.start) {
      dateClauses.push(`published_at >= $${params.length + 1}::timestamptz`);
      params.push(filters.date_range.start);
    }
    if (filters?.date_range?.end) {
      dateClauses.push(`published_at <= $${params.length + 1}::timestamptz`);
      params.push(filters.date_range.end);
    }
    const whereCombined = dateClauses.length
      ? `WHERE (${dateClauses.join(' AND ')}${filters?.include_undated ? ' OR published_at IS NULL' : ''})`
      : '';

    const searchQuery = `
      WITH combined AS (
        SELECT
          m.rid,
          m.content->>'text' as content,
          m.metadata->>'source' as source,
          m.metadata->>'url' as url,
          ts_rank_cd(m.content_tsv, to_tsquery('english', $1)) as rank,
          'strict' as match_type,
          m.published_at
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
          'relaxed' as match_type,
          m.published_at
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
      )
      SELECT * FROM combined
      ${whereCombined}
      ORDER BY rank DESC
      LIMIT $${params.length + 1}
    `;

    // Increased multiplier to ensure OR results are well represented
    // With AND getting ~10 results and OR getting ~10, we need more total results
    params.push(Math.max(topK * 3, 50));
    const results = await pool.query(searchQuery, params);

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
          published_at: row.published_at || null,
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
    const dateClauses: string[] = [];
    const params: any[] = [`%${query}%`, `%${query}%`];
    if (filters?.date_range?.start) {
      dateClauses.push(`m.published_at >= $${params.length + 1}::timestamptz`);
      params.push(filters.date_range.start);
    }
    if (filters?.date_range?.end) {
      dateClauses.push(`m.published_at <= $${params.length + 1}::timestamptz`);
      params.push(filters.date_range.end);
    }
    const andDate = dateClauses.length
      ? ` AND (${dateClauses.join(' AND ')}${filters?.include_undated ? ' OR m.published_at IS NULL' : ''})`
      : '';

    const fallbackQuery = `
      SELECT
        m.rid,
        m.content->>'text' as content,
        m.metadata->>'source' as source,
        m.metadata->>'url' as url,
        0.5 as rank,
        m.published_at
      FROM koi_memories m
      WHERE
        m.content->>'text' ILIKE $1
        AND LENGTH(m.content->>'text') > 50
        ${andDate}
      ORDER BY CASE
        WHEN m.content->>'text' ILIKE $2 THEN 3  -- Exact phrase match
        WHEN m.content->>'text' ILIKE $1 THEN 2  -- Contains all words
        ELSE 1
      END DESC
      LIMIT $${params.length + 1}
    `;

    params.push(topK);
    const results = await pool.query(fallbackQuery, params);

    return results.rows.map(row => ({
      id: row.rid,
      content: row.content.substring(0, 200) + "...",
      similarity: parseFloat(row.rank),
      score: parseFloat(row.rank),
      source: 'keyword' as const,
      metadata: {
        rid: row.rid,
        source: row.source,
        url: row.url,
        published_at: row.published_at || null
      },
      rid: row.rid
    }));
  }
}
app.post('/api/koi/query', async (req, res) => {
  try {
    const { question, user_id = 'web-user', agent_id = 'koi-interface', limit = 10, filters = {} } = req.body;

    if (!question) {
      return res.status(400).json({ error: 'Question is required' });
    }

    const startTime = Date.now();

    // Perform hybrid search with RRF
    // Increased limits to capture more diverse results before fusion
    // Higher keyword limit to ensure important biographical pages (rank ~#11) are included
    // Increased to 20 to capture team/profile pages that rank just outside top-15
    const [vectorResults, keywordResults] = await Promise.all([
      performSemanticSearch(question, 20, filters),
      performKeywordSearch(question, 20, filters)
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
    try {
      await logQuery(pool, {
        query_text: question,
        user_id,
        agent_id,
        confidence_score: confidence,
        triggered_extraction: triggeredExtraction,
        response_time_ms: responseTime,
        results: fusedResults.slice(0, 5)
      });
    } catch (error) {
      console.error('Query logging failed:', error);
    }

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
        rid: r.rid,
        metadata: r.metadata || {}
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

// Podcast chatbot endpoint with LLM synthesis
app.post('/api/podcast/chat', async (req, res) => {
  try {
    const { question, limit = 5 } = req.body;

    if (!question) {
      return res.status(400).json({ error: 'Question is required' });
    }

    // Call the existing hybrid RAG API with larger limit to get podcast results after filtering
    const ragResponse = await fetch('http://localhost:8301/api/koi/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        limit: 50  // Request more results so we can filter to podcasts and still have enough
      })
    });

    if (!ragResponse.ok) {
      return res.status(500).json({ error: 'Search failed', synthesized: false });
    }

    const ragData = await ragResponse.json();
    const allResults = ragData.results || [];

    // Filter to only podcast results, then limit to requested amount
    const filteredResults = allResults.filter((r: any) => {
      const rid = r.rid || '';
      return rid.includes('podcast');
    });
    const results = filteredResults.slice(0, limit || 5);

    console.log(`[Podcast Chat] Filtered ${filteredResults.length} podcast results from ${allResults.length} total, using top ${results.length}`);
    if (results.length > 0) {
      console.log(`[Podcast Chat] Sample RID: ${results[0].rid}`);
    }

    if (results.length === 0) {
      return res.json({
        question,
        answer: 'No relevant information found about that in the podcast.',
        synthesized: false
      });
    }

    // Load podcast metadata
    let podcastData: any = null;
    try {
      const fs = await import('fs');
      const podcastJson = fs.readFileSync('/opt/projects/koi-processor/static/podcast/podcast_map_3d.json', 'utf8');
      podcastData = JSON.parse(podcastJson);
      console.log(`[Podcast Chat] Loaded ${podcastData.episodes?.length || 0} episodes from metadata`);
    } catch (error) {
      console.error('[Podcast Chat] Error loading podcast metadata:', error);
    }

    // Extract episodes and build context for LLM
    const episodesInfo: Record<string, {url: string, episode_int_id: number}> = {};
    const contextParts: string[] = [];

    results.forEach((r: any, idx: number) => {
      const content = r.content || '';
      contextParts.push(`[${idx + 1}] ${content}`);

      // Try to extract episode metadata from RID
      // Format: regen.podcast:podcast_716040283#chunk34
      const rid = r.rid || '';
      const match = rid.match(/:podcast_(\d+)/);
      if (match) {
        const episodeIdStr = `podcast_${match[1]}`;
        console.log(`[Episode Extract] RID: ${rid} -> episode_id: ${episodeIdStr}`);

        if (podcastData && podcastData.episodes) {
          // Find episode in podcast data by episode_id field
          const episode = podcastData.episodes.find((ep: any) => ep.episode_id === episodeIdStr);
          console.log(`[Episode Extract] Found episode:`, episode ? episode.title : 'NOT FOUND');

          if (episode) {
            const episodeTitle = episode.title || episodeIdStr;
            const episodeUrl = episode.url || `https://regen.gaiaai.xyz/podcast`;
            if (!(episodeTitle in episodesInfo)) {
              episodesInfo[episodeTitle] = {
                url: episodeUrl,
                episode_int_id: episode.episode_int_id
              };
              console.log(`[Episode Extract] Added: ${episodeTitle}`);
            }
          }
        }
      } else {
        console.log(`[Episode Extract] No match for RID: ${rid}`);
      }
    });

    console.log(`[Episode Extract] Total episodes found: ${Object.keys(episodesInfo).length}`);

    const context = contextParts.join('\n\n');

    // LLM synthesis with OpenAI
    const openaiKey = process.env.OPENAI_API_KEY;
    if (!openaiKey) {
      return res.json({
        question,
        results: results.slice(0, 5),
        synthesized: false,
        error: 'OpenAI not configured'
      });
    }

    try {
      const systemPrompt = `You are a helpful AI assistant for the Planetary Regeneration Podcast.

Answer questions based ONLY on the provided context from podcast transcripts. Be concise and direct in your response.

Do NOT include any citations, source numbers, or episode lists in your answer. Just provide a clear, informative response to the question.`;

      const userPrompt = `Question: ${question}

Context from podcast:
${context}

Answer the question using the context above.`;

      const openaiResponse = await fetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${openaiKey}`
        },
        body: JSON.stringify({
          model: 'gpt-4o-mini',
          messages: [
            { role: 'system', content: systemPrompt },
            { role: 'user', content: userPrompt }
          ],
          temperature: 0.7,
          max_tokens: 600
        })
      });

      if (!openaiResponse.ok) {
        throw new Error(`OpenAI API error: ${openaiResponse.status}`);
      }

      const openaiData = await openaiResponse.json();
      const answer = openaiData.choices[0].message.content;

      return res.json({
        question,
        answer,
        episodes: episodesInfo,
        synthesized: true,
        total_results: results.length
      });

    } catch (error) {
      console.error('LLM synthesis error:', error);
      return res.json({
        question,
        results: results.slice(0, 5),
        synthesized: false,
        error: error instanceof Error ? error.message : 'LLM error'
      });
    }

  } catch (error) {
    console.error('Podcast chat error:', error);
    res.status(500).json({
      error: 'Internal server error',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Graph Query Endpoint - Direct Apache AGE access
app.post('/api/koi/graph', async (req, res) => {
  try {
    const { query_type, ...params } = req.body;

    if (!query_type) {
      return res.status(400).json({ error: 'query_type is required' });
    }

    // Create a client for this request to manage AGE setup
    const client = await pool.connect();

    try {
      // Load AGE extension and set search path
      await client.query("LOAD 'age'");
      await client.query("SET search_path = ag_catalog, '$user', public");

      let cypherQuery = '';
      let queryParams: any[] = [];

      // Build cypher query based on query_type
      switch (query_type) {
        case 'list_repos':
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$ MATCH (r:Repository) RETURN r.name as name, r.url as url ORDER BY r.name $$) as (name agtype, url agtype)`;
          break;

        case 'find_by_type':
          const entityType = params.entity_type || 'Function';
          const limit = params.limit || 10;
          const repoFilter = params.repo_name ? `WHERE r.name = '${params.repo_name}'` : '';

          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n:${entityType})
            ${repoFilter ? `MATCH (n)-[:DEFINED_IN]->(f:File)-[:IN_REPO]->(r:Repository) ${repoFilter}` : ''}
            RETURN n.name as name, n.signature as signature, n.description as description
            LIMIT ${limit}
          $$) as (name agtype, signature agtype, description agtype)`;
          break;

        case 'search_entities':
          const searchTerm = params.entity_name || '';
          const searchLimit = params.limit || 10;
          const searchRepoFilter = params.repo_name ? `MATCH (n)-[:DEFINED_IN]->(f:File)-[:IN_REPO]->(r:Repository) WHERE r.name = '${params.repo_name}' AND` : 'WHERE';

          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n)
            ${searchRepoFilter} n.name =~ '(?i).*${searchTerm}.*'
            RETURN labels(n)[0] as type, n.name as name, n.signature as signature, n.description as description
            LIMIT ${searchLimit}
          $$) as (type agtype, name agtype, signature agtype, description agtype)`;
          break;

        case 'list_modules':
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (m:Module)
            RETURN m.name as name, m.path as path
            ORDER BY m.name
          $$) as (name agtype, path agtype)`;
          break;

        case 'get_module':
          const moduleName = params.module_name || '';
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (m:Module {name: '${moduleName}'})
            OPTIONAL MATCH (m)<-[:IN_MODULE]-(e)
            RETURN m.name as module_name, m.path as module_path, labels(e)[0] as entity_type, e.name as entity_name
          $$) as (module_name agtype, module_path agtype, entity_type agtype, entity_name agtype)`;
          break;

        case 'keeper_for_msg':
          const msgName = params.entity_name || '';
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (msg {name: '${msgName}'})-[:HANDLED_BY]->(k:Keeper)
            RETURN k.name as keeper_name, k.signature as signature
          $$) as (keeper_name agtype, signature agtype)`;
          break;

        case 'msgs_for_keeper':
          const keeperName = params.entity_name || '';
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (k:Keeper {name: '${keeperName}'})<-[:HANDLED_BY]-(msg)
            RETURN msg.name as msg_name, msg.signature as signature
          $$) as (msg_name agtype, signature agtype)`;
          break;

        case 'related_entities':
          const entityName = params.entity_name || '';
          const relatedLimit = params.limit || 10;
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n {name: '${entityName}'})-[r]-(related)
            RETURN labels(related)[0] as type, related.name as name, type(r) as relationship
            LIMIT ${relatedLimit}
          $$) as (type agtype, name agtype, relationship agtype)`;
          break;

        default:
          return res.status(400).json({ error: `Unknown query_type: ${query_type}` });
      }

      // Execute the cypher query
      const result = await client.query(cypherQuery, queryParams);

      // Convert agtype results to plain JSON
      const rows = result.rows.map(row => {
        const converted: any = {};
        for (const key in row) {
          const value = row[key];
          // AGE returns values as JSON strings, parse them
          if (value === null || value === undefined) {
            converted[key] = null;
          } else if (typeof value === 'string') {
            try {
              // Try to parse as JSON (AGE agtype format)
              const parsed = JSON.parse(value);
              converted[key] = parsed;
            } catch {
              // If not JSON, keep as string
              converted[key] = value;
            }
          } else {
            converted[key] = value;
          }
        }
        return converted;
      });

      res.json({
        query_type,
        total_results: rows.length,
        results: rows
      });

    } finally {
      client.release();
    }

  } catch (error) {
    console.error('Graph query error:', error);
    res.status(500).json({
      error: 'Graph query failed',
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
// Note: /api/koi/graph is handled directly above, not proxied
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
