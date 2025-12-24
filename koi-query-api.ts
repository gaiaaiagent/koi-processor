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
} from "./bge-mcp-ts/adaptive-features.ts";

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

// Helper function to fix duplicated paths in entity data
function fixEntityPaths(entity: any): any {
  if (!entity || typeof entity !== "object") return entity;
  
  // Fix file_path - handle multiple prefix patterns
  if (entity.file_path && entity.repo) {
    let path = entity.file_path;
    
    // Pattern 1: /opt/projects/regen-repos/<repo>/...
    const absPrefix = `/opt/projects/regen-repos/${entity.repo}/`;
    if (path.startsWith(absPrefix)) {
      path = path.substring(absPrefix.length);
    }
    
    // Pattern 2: regen-network/<repo>/...
    const regenPrefix = `regen-network/${entity.repo}/`;
    if (path.startsWith(regenPrefix)) {
      path = path.substring(regenPrefix.length);
    }
    
    // Pattern 3: <repo>/... (e.g., regen-ledger/x/...)
    const repoPrefix = `${entity.repo}/`;
    if (path.startsWith(repoPrefix)) {
      path = path.substring(repoPrefix.length);
    }
    
    entity.file_path = path;
  }
  
  // Reconstruct github_url with correct path
  if (entity.file_path && entity.repo) {
    const branch = entity.commit_sha && entity.commit_sha !== "None" ? entity.commit_sha : (entity.branch || "main");
    entity.github_url = `https://github.com/regen-network/${entity.repo}/blob/${branch}/${entity.file_path}${entity.line_number ? "#L" + entity.line_number : ""}`;
  }
  
  return entity;
}

// No need to set probes or other runtime parameters

// Middleware
app.use(cors());
app.use(express.json());

// Generate a random session token (UUID v4 format)
function generateSessionToken(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // Version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // Variant 1
  const hex = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

// Session token lifetime (1 hour - shorter than Google OAuth token)
const SESSION_TOKEN_LIFETIME_MS = 60 * 60 * 1000;

// Hash a token using SHA-256 for secure storage/lookup
async function hashToken(token: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(token);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

// Create a new session token for an authenticated user
// NOTE: This is kept for internal use only - MCP clients should use the device_code flow
// SECURITY: Plain token is returned to caller but NEVER stored in database
async function createSessionToken(userEmail: string, clientInfo?: string): Promise<string> {
  const sessionToken = generateSessionToken();
  const tokenHash = await hashToken(sessionToken);
  const expiresAt = new Date(Date.now() + SESSION_TOKEN_LIFETIME_MS);

  // Store ONLY the hash in session_tokens - plain token never touches the DB
  await pool.query(
    `INSERT INTO session_tokens (token_hash, user_email, expires_at, client_info)
     VALUES ($1, $2, $3, $4)`,
    [tokenHash, userEmail, expiresAt, clientInfo || null]
  );

  console.log(`[Auth] Created session token for ${userEmail}, expires at ${expiresAt.toISOString()}`);
  return sessionToken;  // Plain token only exists in memory, returned to caller
}

// Validate a session token - returns user email if valid, null otherwise
// SECURITY: This validates by hashing the token and comparing to stored hash
// This prevents timing attacks and protects against database leaks
async function validateSessionToken(sessionToken: string | undefined): Promise<string | null> {
  if (!sessionToken) return null;

  try {
    const tokenHash = await hashToken(sessionToken);

    const result = await pool.query(
      `SELECT user_email, expires_at FROM session_tokens
       WHERE token_hash = $1
       AND expires_at > NOW()
       AND revoked_at IS NULL`,
      [tokenHash]
    );

    if (result.rows.length === 0) {
      return null; // Token not found, expired, or revoked
    }

    const userEmail = result.rows[0].user_email;

    // Additional validation: ensure it's a @regen.network email
    if (!userEmail.endsWith('@regen.network')) {
      console.warn(`Session token found but email domain not @regen.network: ${userEmail}`);
      return null;
    }

    return userEmail;
  } catch (error) {
    console.error('Session token validation error:', error);
    return null;
  }
}

// Check if user has valid OAuth token (for creating session tokens)
async function hasValidOAuthToken(userEmail: string): Promise<boolean> {
  try {
    const result = await pool.query(
      `SELECT 1 FROM oauth_tokens
       WHERE user_email = $1 AND token_expiry > NOW()`,
      [userEmail]
    );
    return result.rows.length > 0;
  } catch (error) {
    console.error('OAuth token check error:', error);
    return false;
  }
}

// Build privacy filter clause based on authentication status
function buildPrivacyFilter(isAuthenticated: boolean, tableAlias: string = 'm'): string {
  if (isAuthenticated) {
    return ''; // Authenticated users see all data
  }
  // Unauthenticated users only see public data
  return ` AND (${tableAlias}.is_private = FALSE OR ${tableAlias}.is_private IS NULL)`;
}

// Entity-based graph search using koi_entity_chunk_links
// Detects entities in query and returns memories where those entities appear
async function performEntitySearch(query: string, topK: number = 20, privacyFilter: string = '') {
  try {
    console.log(`[EntitySearch] Starting for query: "${query}"`);

    // Extract potential entity names from query (words and phrases of 2-4 words)
    const words = query.toLowerCase()
      .replace(/[^\w\s]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length >= 3);

    console.log(`[EntitySearch] Words extracted: ${words.join(', ')}`);

    if (words.length === 0) {
      console.log('[EntitySearch] No words >= 3 chars, returning empty');
      return [];
    }

    // Build patterns to match: individual words and adjacent word pairs/triples
    const patterns: string[] = [...words];
    for (let i = 0; i < words.length - 1; i++) {
      patterns.push(`${words[i]} ${words[i+1]}`);
      if (i < words.length - 2) {
        patterns.push(`${words[i]} ${words[i+1]} ${words[i+2]}`);
      }
    }

    // Query for entities matching these patterns
    // Uses source-diversity sampling to prevent any single source from dominating
    const entityQuery = `
      WITH matched_entities AS (
        SELECT DISTINCT entity_name_lower, entity_name, entity_type,
          LENGTH(entity_name_lower) as entity_length
        FROM koi_entity_chunk_links
        WHERE entity_name_lower = ANY($1)
        LIMIT 50
      ),
      entity_memories AS (
        SELECT
          l.chunk_rid,
          l.document_rid,
          array_agg(DISTINCT l.entity_name) as entities_matched,
          COUNT(DISTINCT l.entity_name_lower) as entity_count,
          MAX(me.entity_length) as max_entity_length
        FROM koi_entity_chunk_links l
        JOIN matched_entities me ON l.entity_name_lower = me.entity_name_lower
        GROUP BY l.chunk_rid, l.document_rid
      ),
      with_source AS (
        SELECT
          m.rid,
          m.content->>'text' as content,
          m.metadata->>'source' as source,
          m.metadata->>'url' as url,
          em.entities_matched,
          em.entity_count,
          em.max_entity_length,
          m.published_at,
          CASE
            WHEN m.rid LIKE 'orn:web.page:%' THEN 'web'
            WHEN m.rid LIKE 'regen.github:%' THEN 'github'
            WHEN m.rid LIKE 'regen.gitlab:%' THEN 'gitlab'
            ELSE 'other'
          END as source_type,
          CASE
            WHEN m.rid LIKE 'orn:web.page:regen.network/%' THEN 'main'
            WHEN m.rid LIKE 'orn:web.page:forum.regen.network/%' THEN 'forum'
            WHEN m.rid LIKE 'orn:web.page:registry.regen.network/%' THEN 'registry'
            WHEN m.rid LIKE 'orn:web.page:guides.regen.network/%' THEN 'guides'
            ELSE NULL
          END as web_domain
        FROM entity_memories em
        JOIN koi_memories m ON m.id::text = em.chunk_rid
        WHERE m.superseded_at IS NULL
          AND m.content->>'text' IS NOT NULL
          ${privacyFilter}
      ),
      non_web_ranked AS (
        SELECT *, ROW_NUMBER() OVER (
          PARTITION BY source_type ORDER BY max_entity_length DESC, entity_count DESC
        ) as source_rank
        FROM with_source WHERE source_type != 'web'
      ),
      web_domain_ranked AS (
        SELECT *, ROW_NUMBER() OVER (
          PARTITION BY web_domain ORDER BY max_entity_length DESC, entity_count DESC
        ) as domain_rank
        FROM with_source WHERE source_type = 'web'
      ),
      web_diverse AS (
        SELECT *, ROW_NUMBER() OVER (ORDER BY domain_rank, web_domain) as source_rank
        FROM web_domain_ranked WHERE domain_rank <= 10
      ),
      combined AS (
        SELECT rid, content, source, url, entities_matched, entity_count,
               max_entity_length, published_at
        FROM non_web_ranked WHERE source_rank <= 25
        UNION ALL
        SELECT rid, content, source, url, entities_matched, entity_count,
               max_entity_length, published_at
        FROM web_diverse WHERE source_rank <= 50
      ),
      -- Deduplicate by content hash to remove duplicates from multiple sensor runs
      deduplicated AS (
        SELECT *,
          ROW_NUMBER() OVER (
            PARTITION BY md5(content)
            ORDER BY published_at DESC NULLS LAST, entity_count DESC
          ) as content_rank
        FROM combined
      )
      SELECT rid, content, source, url, entities_matched, entity_count,
             max_entity_length, published_at
      FROM deduplicated
      WHERE content_rank = 1
      ORDER BY max_entity_length DESC
      LIMIT $2
    `;

    console.log(`[EntitySearch] Patterns to match: ${patterns.slice(0, 10).join(', ')}`);

    const results = await pool.query(entityQuery, [patterns, topK]);

    console.log(`[EntitySearch] Query returned ${results.rows.length} rows`);
    if (results.rows.length > 0) {
      console.log(`[EntitySearch] Found ${results.rows.length} memories for entities: ${patterns.slice(0, 5).join(', ')}...`);
      console.log(`[EntitySearch] First result RID: ${results.rows[0]?.rid}`);
    } else {
      console.log(`[EntitySearch] No matches found for patterns`);
    }

    // Calculate scores based on entity count (normalized)
    const maxCount = results.rows.length > 0
      ? Math.max(...results.rows.map(r => parseInt(r.entity_count)))
      : 1;

    return results.rows.map(row => ({
      id: row.rid,
      content: row.content?.substring(0, 200) + "...",
      similarity: parseInt(row.entity_count) / maxCount,
      score: parseInt(row.entity_count) / maxCount,
      source: 'graph' as const,
      metadata: {
        rid: row.rid,
        source: row.source,
        url: row.url,
        entities_matched: row.entities_matched,
        entity_count: parseInt(row.entity_count),
        published_at: row.published_at || null
      },
      rid: row.rid
    }));

  } catch (error) {
    console.error('[EntitySearch] Error:', error);
    return [];
  }
}

// Semantic search using BGE embeddings
async function performSemanticSearch(query: string, topK: number = 10, filters?: any, privacyFilter: string = '') {
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
        AND m.superseded_at IS NULL
          AND (
            m.content->>'text' ILIKE $1
            OR m.content->>'text' ILIKE $2
          )
          ${whereDate}
          ${privacyFilter}
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
      WITH vector_results AS (
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
          AND m.superseded_at IS NULL
          ${andDate}
          ${privacyFilter}
        ORDER BY e.dim_1024 <=> $1::vector
        LIMIT $${params.length + 1} * 3
      ),
      -- Deduplicate by content hash to remove duplicates from multiple sensor runs
      deduplicated AS (
        SELECT *,
          ROW_NUMBER() OVER (
            PARTITION BY md5(content)
            ORDER BY similarity DESC
          ) as content_rank
        FROM vector_results
      )
      SELECT rid, content, source, url, distance, similarity, published_at
      FROM deduplicated
      WHERE content_rank = 1
      ORDER BY similarity DESC
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
async function performKeywordSearch(query: string, topK: number = 10, filters?: any, privacyFilter: string = '') {
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
        AND m.superseded_at IS NULL
          ${privacyFilter}

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
        AND m.superseded_at IS NULL
          ${privacyFilter}
          AND NOT EXISTS (
            SELECT 1 FROM koi_memories m2
            WHERE m2.rid = m.rid
            AND m2.content_tsv @@ to_tsquery('english', $1)
          )
      ),
      -- Deduplicate by content hash to remove duplicates from multiple sensor runs
      deduplicated AS (
        SELECT *,
          ROW_NUMBER() OVER (
            PARTITION BY md5(content)
            ORDER BY rank DESC, match_type
          ) as content_rank
        FROM combined
        ${whereCombined}
      )
      SELECT rid, content, source, url, rank, match_type, published_at
      FROM deduplicated
      WHERE content_rank = 1
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
        AND m.superseded_at IS NULL
        ${andDate}
        ${privacyFilter}
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

    // Extract session token from Authorization header and validate
    // Format: "Bearer <session_token>" - NOT the Google OAuth token
    const authHeader = req.headers['authorization'] as string | undefined;
    const sessionToken = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : undefined;

    // Validate session token and get authenticated user email (if any)
    const authenticatedEmail = await validateSessionToken(sessionToken);
    const isAuthenticated = !!authenticatedEmail;
    const privacyFilter = buildPrivacyFilter(isAuthenticated);

    // Log auth status for debugging (use X-User-Email for logging if no token, but it doesn't grant access)
    const logEmail = authenticatedEmail || req.headers['x-user-email'] as string | undefined;
    if (logEmail || sessionToken) {
      console.log(`[Query] User: ${logEmail || 'unknown'}, Authenticated: ${isAuthenticated}${sessionToken ? ' (session token provided)' : ''}`);
    }

    // Perform hybrid search with RRF
    // Includes vector (semantic), entity (graph), and keyword search
    // Higher limits to capture diverse results before fusion
    let vectorResults: any[] = [];
    let entityResults: any[] = [];
    let keywordResults: any[] = [];

    try {
      [vectorResults, entityResults, keywordResults] = await Promise.all([
        performSemanticSearch(question, 20, filters, privacyFilter),
        performEntitySearch(question, 100, privacyFilter).catch(e => {
          console.error('[EntitySearch] Error:', e);
          return [];
        }),
        performKeywordSearch(question, 20, filters, privacyFilter)
      ]);
    } catch (err) {
      console.error('[Search] Error in parallel search:', err);
    }

    // Log search results counts
    console.log(`[Search] Results - Vector: ${vectorResults.length}, Entity: ${entityResults.length}, Keyword: ${keywordResults.length}`);

    // Apply Reciprocal Rank Fusion with entity/graph results
    const fusedResults = reciprocalRankFusion(vectorResults, entityResults, keywordResults);
    
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
          // Get all unique repository names from entities
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n) WHERE n.repo IS NOT NULL
            WITH DISTINCT n.repo as repo_name, count(*) as entity_count
            RETURN {name: repo_name} as repo, entity_count
            ORDER BY repo_name
          $$) as (repo agtype, entity_count agtype)`;
          break;

        case 'find_by_type':
          const entityType = params.entity_type || 'Function';
          const limit = params.limit || 10;
          const repoFilter = params.repo_name ? `WHERE r.name = '${params.repo_name}'` : '';

          // Match entity type directly by graph label
          const typeMatch = `MATCH (n:${entityType})`;

          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            ${typeMatch}
            ${repoFilter ? `MATCH (n)-[:DEFINED_IN]->(f:File)-[:IN_REPO]->(r:Repository) ${repoFilter}` : ''}
            RETURN properties(n) as entity
            LIMIT ${limit}
          $$) as (entity agtype)`;
          break;

        case 'search_entities':
          const searchTerm = params.entity_name || '';
          const searchLimit = params.limit || 10;
          const searchRepoFilter = params.repo_name ? `WHERE n.repo = '${params.repo_name}' AND` : 'WHERE';

          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n)
            ${searchRepoFilter} n.name =~ '(?i).*${searchTerm}.*'
            RETURN labels(n)[0] as type, properties(n) as entity
            LIMIT ${searchLimit}
          $$) as (type agtype, entity agtype)`;
          break;

        case 'list_modules':
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (m:Module)
            RETURN properties(m) as module
            ORDER BY m.name
          $$) as (module agtype)`;
          break;

        case 'get_module':
          const moduleName = params.module_name || '';
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (m:Module {name: '${moduleName}'})
            OPTIONAL MATCH (m)<-[:IN_MODULE]-(e)
            RETURN properties(m) as module, labels(e)[0] as entity_type, properties(e) as entity
          $$) as (module agtype, entity_type agtype, entity agtype)`;
          break;

        case 'keeper_for_msg':
          const msgName = params.entity_name || '';
          // Updated to query generic Struct with domain_type='keeper'
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (msg {name: '${msgName}'})-[:HANDLED_BY]->(k:Struct)
            WHERE k.domain_type = 'keeper'
            RETURN properties(k) as keeper
          $$) as (keeper agtype)`;
          break;

        case 'msgs_for_keeper':
          const keeperName = params.entity_name || '';
          // Updated to query generic Struct with domain_type='keeper' and domain_type='message'
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (k:Struct {name: '${keeperName}'})<-[:HANDLED_BY]-(msg:Struct)
            WHERE k.domain_type = 'keeper' AND msg.domain_type = 'message'
            RETURN properties(msg) as message
          $$) as (message agtype)`;
          break;

        case 'related_entities':
          const entityName = params.entity_name || '';
          const relatedLimit = params.limit || 10;
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n {name: '${entityName}'})-[r]-(related)
            RETURN labels(related)[0] as type, properties(related) as entity, type(r) as relationship
            LIMIT ${relatedLimit}
          $$) as (type agtype, entity agtype, relationship agtype)`;
          break;

        case 'list_entity_types':
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n)
            WITH labels(n)[0] as entity_type, count(*) as count
            WHERE entity_type IS NOT NULL
            RETURN entity_type, count
            ORDER BY count DESC
          $$) as (entity_type agtype, count agtype)`;
          break;

        case 'get_entity_stats':
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n)
            WITH labels(n)[0] as entity_type,
                 count(*) as count,
                 collect(DISTINCT n.language)[0..5] as languages,
                 collect(DISTINCT n.repo)[0..5] as repos
            WHERE entity_type IS NOT NULL
            RETURN entity_type, count, languages, repos
            ORDER BY count DESC
          $$) as (entity_type agtype, count agtype, languages agtype, repos agtype)`;
          break;

        case 'list_concepts':
          // List all concepts with their descriptions
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (c:Concept)
            RETURN properties(c) as concept
            ORDER BY c.name
          $$) as (concept agtype)`;
          break;

        case 'explain_concept':
          // Get a concept and its related entities via EXPLAINS edges
          const conceptName = params.concept_name || '';
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (c:Concept {name: '${conceptName}'})
            OPTIONAL MATCH (c)-[:EXPLAINS]->(e)
            RETURN properties(c) as concept, 
                   labels(e)[0] as entity_type,
                   properties(e) as entity
          $$) as (concept agtype, entity_type agtype, entity agtype)`;
          break;

        case 'find_concept_for_query':
          // Find concepts matching a natural language query via keywords
          const userQuery = params.query || '';
          const queryLower = userQuery.toLowerCase();
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (c:Concept)
            WHERE c.keywords CONTAINS '${queryLower}'
               OR toLower(c.name) CONTAINS '${queryLower}'
               OR toLower(c.description) CONTAINS '${queryLower}'
            RETURN properties(c) as concept
            LIMIT 5
          $$) as (concept agtype)`;
          break;
        case 'find_callers':
          // Find all functions/methods that call a given entity
          const targetName = params.entity_name || '';
          const callersLimit = params.limit || 50;
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (caller)-[:CALLS]->(callee)
            WHERE callee.name = '${targetName}'
            RETURN properties(caller) as caller,
                   properties(callee) as callee
            LIMIT ${callersLimit}
          $$) as (caller agtype, callee agtype)`;
          break;

        case 'find_callees':
          // Find all functions/methods called by a given entity
          const callerName = params.entity_name || '';
          const calleesLimit = params.limit || 50;
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (caller {name: '${callerName}'})-[:CALLS]->(callee)
            RETURN properties(caller) as caller,
                   properties(callee) as callee
            LIMIT ${calleesLimit}
          $$) as (caller agtype, callee agtype)`;
          break;

        case 'find_call_graph':
          // Return the local call graph (1-2 hops) around an entity
          const centerEntity = params.entity_name || '';
          const hops = params.hops || 1;
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (center {name: '${centerEntity}'})
            OPTIONAL MATCH (caller)-[:CALLS*1..${hops}]->(center)
            OPTIONAL MATCH (center)-[:CALLS*1..${hops}]->(callee)
            RETURN properties(center) as center,
                   collect(DISTINCT properties(caller)) as callers,
                   collect(DISTINCT properties(callee)) as callees
          $$) as (center agtype, callers agtype, callees agtype)`;
          break;

        case 'find_orphaned_code':
          // Find entities with no incoming or outgoing CALLS edges
          const orphanType = params.entity_type || 'Function';
          const orphanLimit = params.limit || 100;
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH (n)
            WHERE (n.entity_type = '${orphanType}' OR labels(n)[0] = '${orphanType}')
            OPTIONAL MATCH (n)-[r_out:CALLS]->()
            OPTIONAL MATCH (n)<-[r_in:CALLS]-()
            WHERE r_out IS NULL AND r_in IS NULL
            RETURN properties(n) as orphan
            LIMIT ${orphanLimit}
          $$) as (orphan agtype)`;
          break;

        case 'trace_call_chain':
          // Find the full call path from entity A to entity B
          const fromName = params.from || '';
          const toName = params.to || '';
          const maxDepth = params.max_depth || 5;
          const chainLimit = params.limit || 10;
          cypherQuery = `SELECT * FROM cypher('regen_graph', $$
            MATCH path = (source {name: '${fromName}'})-[:CALLS*1..${maxDepth}]->(target {name: '${toName}'})
            RETURN nodes(path) as chain
            LIMIT ${chainLimit}
          $$) as (chain agtype)`;
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


      // Fix duplicated paths in entity data
      const fixedRows = rows.map(row => {
        const fixed: any = { ...row };
        // Fix entity objects
        if (fixed.entity) fixed.entity = fixEntityPaths(fixed.entity);
        if (fixed.caller) fixed.caller = fixEntityPaths(fixed.caller);
        if (fixed.callee) fixed.callee = fixEntityPaths(fixed.callee);
        if (fixed.orphan) fixed.orphan = fixEntityPaths(fixed.orphan);
        // For chain results (array of entities)
        if (Array.isArray(fixed.chain)) {
          fixed.chain = fixed.chain.map((e: any) => fixEntityPaths(e));
        }
        return fixed;
      });

      res.json({
        query_type,
        total_results: fixedRows.length,
        results: fixedRows
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

// Auth status check - for MCP server to validate user authentication
// SECURITY: Uses device_code binding to prevent IDOR attacks
// Session tokens are returned ONCE only to the client that initiated the auth
app.get('/api/koi/auth/status', async (req, res) => {
  try {
    // Method 1: validate existing session_token from Authorization header
    const authHeader = req.headers['authorization'] as string | undefined;
    const sessionToken = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : undefined;

    // Also support token in query param for easier testing
    const queryToken = req.query.session_token as string | undefined;
    const tokenToValidate = sessionToken || queryToken;

    if (tokenToValidate) {
      // Validate the provided session token (uses hash comparison)
      const authenticatedEmail = await validateSessionToken(tokenToValidate);

      if (!authenticatedEmail) {
        return res.json({
          authenticated: false,
          reason: 'Invalid or expired session token'
        });
      }

      return res.json({
        authenticated: true,
        user_email: authenticatedEmail,
        timestamp: new Date().toISOString()
      });
    }

    // Method 2: Poll using device_code (SECURE - prevents IDOR)
    // Only the client that initiated the auth can retrieve the session token
    const deviceCode = req.query.device_code as string | undefined;

    if (!deviceCode || deviceCode.length < 32) {
      return res.json({
        authenticated: false,
        reason: 'No session token or device_code provided. Use device_code to poll for auth status.'
      });
    }

    // Look up auth request by device_code
    // SECURITY: session_token is stored temporarily here (not in long-lived session_tokens)
    const authRequest = await pool.query(
      `SELECT id, user_email, status, session_token, expires_at
       FROM auth_requests
       WHERE device_code = $1`,
      [deviceCode]
    );

    if (authRequest.rows.length === 0) {
      return res.json({
        status: 'not_found',
        authenticated: false,
        reason: 'Invalid or expired auth request'
      });
    }

    const row = authRequest.rows[0];

    if (new Date(row.expires_at).getTime() < Date.now()) {
      return res.json({
        status: 'expired',
        authenticated: false,
        reason: 'Auth request expired'
      });
    }

    if (row.status === 'pending') {
      return res.json({
        status: 'pending',
        authenticated: false,
        reason: 'User has not completed authentication yet'
      });
    }

    if (row.status === 'rejected') {
      return res.json({
        status: 'rejected',
        authenticated: false,
        reason: 'Email domain not allowed'
      });
    }

    if (row.status === 'used') {
      // Token was already retrieved - don't return it again
      return res.json({
        status: 'already_retrieved',
        authenticated: true,
        user_email: row.user_email,
        reason: 'Session token was already retrieved. Use the token you received earlier.'
      });
    }

    if (row.status === 'authenticated') {
      // First time polling after auth - get plain token from auth_requests
      // SECURITY: Plain token is stored temporarily in auth_requests (short-lived)
      // NOT in session_tokens (long-lived, stores only hashes)
      const plainToken = row.session_token;

      if (!plainToken) {
        return res.json({
          status: 'error',
          authenticated: false,
          reason: 'Session token not found or already retrieved'
        });
      }

      // Mark as used and NULL out the plain token (security: don't keep it around)
      await pool.query(
        `UPDATE auth_requests
         SET status = 'used', used_at = CURRENT_TIMESTAMP, session_token = NULL
         WHERE device_code = $1`,
        [deviceCode]
      );

      console.log(`[Auth] Session token retrieved for ${row.user_email} via device_code ${deviceCode.substring(0, 8)}...`);

      const expiresAt = new Date(Date.now() + SESSION_TOKEN_LIFETIME_MS);

      return res.json({
        status: 'authenticated',
        authenticated: true,
        user_email: row.user_email,
        session_token: plainToken,  // Plain token, returned ONCE then NULLed from DB
        token_expiry: expiresAt.toISOString(),
        timestamp: new Date().toISOString()
      });
    }

    // Unknown status
    return res.json({
      status: row.status,
      authenticated: false
    });

  } catch (error) {
    console.error('Auth status check error:', error);
    res.status(500).json({
      authenticated: false,
      error: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Default type priority (higher = more preferred) - matches Python polysemy_resolver.py
const DEFAULT_TYPE_PRIORITY: Record<string, number> = {
  'TECHNOLOGY': 100,
  'PROJECT': 90,
  'ORGANIZATION': 80,
  'CONCEPT': 70,
  'STANDARD': 60,
  'PERSON': 50,
  'PROCESS': 40,
  'MATERIAL': 35,
  'MODULE': 30,
  'LOCATION': 25,
  'EVENT': 20,
  'VALIDATOR': 15,
  'CREDIT_CLASS': 10,
  'GOVERNANCE_PROPOSAL': 5,
  'EVIDENCE': 4,
  'CLAIM': 3,
  'QUESTION': 2,
  'API_MESSAGE': 1,
  'LICENSE': 0,
  'KEEPER': 0,
};

// Polysemy-aware entity resolution endpoint
// GET /api/koi/entity/resolve?label=...&type_hint=...&limit=5
app.get('/api/koi/entity/resolve', async (req, res) => {
  try {
    const label = (req.query.label as string || '').trim();
    const typeHint = (req.query.type_hint as string || '').trim().toUpperCase() || null;
    const limit = Math.min(Math.max(parseInt(req.query.limit as string) || 5, 1), 20);

    if (!label) {
      return res.status(400).json({ error: 'label parameter is required' });
    }

    // Query for entity variants matching the label
    const query = `
      WITH entity_matches AS (
        SELECT
          id,
          entity_text,
          entity_type,
          normalized_text,
          occurrence_count,
          fuseki_uri
        FROM entity_registry
        WHERE LOWER(TRIM(normalized_text)) = LOWER(TRIM($1))
      ),
      rel_counts AS (
        SELECT
          e.id,
          COALESCE(subj.subj_count, 0) + COALESCE(obj.obj_count, 0) as relationship_count
        FROM entity_matches e
        LEFT JOIN (
          SELECT subject_entity_id, COUNT(*) as subj_count
          FROM koi_relationships
          GROUP BY subject_entity_id
        ) subj ON e.id = subj.subject_entity_id
        LEFT JOIN (
          SELECT object_entity_id, COUNT(*) as obj_count
          FROM koi_relationships
          GROUP BY object_entity_id
        ) obj ON e.id = obj.object_entity_id
      )
      SELECT
        e.id,
        e.entity_text,
        e.entity_type,
        e.normalized_text,
        e.occurrence_count,
        e.fuseki_uri,
        r.relationship_count
      FROM entity_matches e
      JOIN rel_counts r ON e.id = r.id
      ORDER BY e.occurrence_count DESC, r.relationship_count DESC
    `;

    const result = await pool.query(query, [label]);
    const variants = result.rows;

    if (variants.length === 0) {
      return res.json({
        query_label: label,
        type_hint: typeHint,
        variant_count: 0,
        winner: null,
        alternatives: [],
        is_polysemy: false,
        resolution_method: 'no_match'
      });
    }

    // Compute scores for all variants
    const scoredVariants = variants.map(v => {
      const occScore = parseInt(v.occurrence_count) * 1000;
      const relScore = parseInt(v.relationship_count) * 100;
      const typePriority = DEFAULT_TYPE_PRIORITY[v.entity_type] || 0;
      const typeScore = typePriority * 10;

      let totalScore = occScore + relScore + typeScore;
      const reasons: string[] = [
        `occ=${v.occurrence_count}`,
        `rels=${v.relationship_count}`,
        `type_pri=${typePriority}`
      ];

      // Boost if matches type hint
      if (typeHint && v.entity_type.toUpperCase() === typeHint) {
        totalScore += 50000;
        reasons.push('type_hint_match=+50k');
      }

      return {
        uri: v.fuseki_uri,
        entity_text: v.entity_text,
        entity_type: v.entity_type,
        occurrence_count: parseInt(v.occurrence_count),
        relationship_count: parseInt(v.relationship_count),
        score: totalScore,
        score_breakdown: reasons.join(', ')
      };
    });

    // Sort by score descending
    scoredVariants.sort((a, b) => b.score - a.score);

    // Determine winner and alternatives
    const winner = scoredVariants[0];
    const alternatives = scoredVariants.slice(1, limit);

    // Determine if polysemy exists
    const uniqueTypes = new Set(scoredVariants.map(v => v.entity_type));
    const isPolysemy = uniqueTypes.size > 1;

    // Determine resolution method
    let resolutionMethod = 'highest_combined_score';
    if (typeHint && winner.entity_type.toUpperCase() === typeHint) {
      resolutionMethod = 'type_hint_match';
    } else {
      const totalOcc = scoredVariants.reduce((sum, v) => sum + v.occurrence_count, 0);
      const totalRels = scoredVariants.reduce((sum, v) => sum + v.relationship_count, 0);

      if (winner.occurrence_count > totalOcc * 0.5) {
        resolutionMethod = 'dominant_occurrence';
      } else if (winner.relationship_count > totalRels * 0.5) {
        resolutionMethod = 'dominant_connectivity';
      }
    }

    res.json({
      query_label: label,
      type_hint: typeHint,
      variant_count: scoredVariants.length,
      winner: winner,
      alternatives: alternatives,
      is_polysemy: isPolysemy,
      resolution_method: resolutionMethod
    });

  } catch (error) {
    console.error('Entity resolve error:', error);
    res.status(500).json({
      error: 'Entity resolution failed',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Internal helper: resolve entity by label or URI
// Returns { entity_id, uri, entity_text, entity_type } or null
async function resolveEntityInternal(
  label: string | null,
  uri: string | null,
  typeHint: string | null
): Promise<{
  entity_id: number;
  uri: string;
  entity_text: string;
  entity_type: string;
  occurrence_count: number;
} | null> {
  if (uri) {
    // Direct URI lookup
    const result = await pool.query(
      `SELECT id, fuseki_uri, entity_text, entity_type, occurrence_count
       FROM entity_registry
       WHERE fuseki_uri = $1`,
      [uri]
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      entity_id: row.id,
      uri: row.fuseki_uri,
      entity_text: row.entity_text,
      entity_type: row.entity_type,
      occurrence_count: parseInt(row.occurrence_count),
    };
  }

  if (!label) return null;

  // Use the same logic as /api/koi/entity/resolve
  const query = `
    WITH entity_matches AS (
      SELECT
        id,
        entity_text,
        entity_type,
        normalized_text,
        occurrence_count,
        fuseki_uri
      FROM entity_registry
      WHERE LOWER(TRIM(normalized_text)) = LOWER(TRIM($1))
    ),
    rel_counts AS (
      SELECT
        e.id,
        COALESCE(subj.subj_count, 0) + COALESCE(obj.obj_count, 0) as relationship_count
      FROM entity_matches e
      LEFT JOIN (
        SELECT subject_entity_id, COUNT(*) as subj_count
        FROM koi_relationships
        GROUP BY subject_entity_id
      ) subj ON e.id = subj.subject_entity_id
      LEFT JOIN (
        SELECT object_entity_id, COUNT(*) as obj_count
        FROM koi_relationships
        GROUP BY object_entity_id
      ) obj ON e.id = obj.object_entity_id
    )
    SELECT
      e.id,
      e.entity_text,
      e.entity_type,
      e.normalized_text,
      e.occurrence_count,
      e.fuseki_uri,
      r.relationship_count
    FROM entity_matches e
    JOIN rel_counts r ON e.id = r.id
    ORDER BY e.occurrence_count DESC, r.relationship_count DESC
  `;

  const result = await pool.query(query, [label]);
  if (result.rows.length === 0) return null;

  // Score and find winner (same logic as /api/koi/entity/resolve)
  const variants = result.rows.map(v => {
    const occScore = parseInt(v.occurrence_count) * 1000;
    const relScore = parseInt(v.relationship_count) * 100;
    const typePriority = DEFAULT_TYPE_PRIORITY[v.entity_type] || 0;
    const typeScore = typePriority * 10;

    let totalScore = occScore + relScore + typeScore;

    // Boost if matches type hint
    if (typeHint && v.entity_type.toUpperCase() === typeHint.toUpperCase()) {
      totalScore += 50000;
    }

    return { ...v, score: totalScore };
  });

  variants.sort((a, b) => b.score - a.score);
  const winner = variants[0];

  return {
    entity_id: winner.id,
    uri: winner.fuseki_uri,
    entity_text: winner.entity_text,
    entity_type: winner.entity_type,
    occurrence_count: parseInt(winner.occurrence_count),
  };
}

// Graph neighborhood endpoint - query local graph structure
// GET /api/koi/entity/neighborhood?label=...&uri=...&type_hint=...&limit=50&direction=both
app.get('/api/koi/entity/neighborhood', async (req, res) => {
  try {
    const label = (req.query.label as string || '').trim() || null;
    const uri = (req.query.uri as string || '').trim() || null;
    const typeHint = (req.query.type_hint as string || '').trim().toUpperCase() || null;
    const limit = Math.min(Math.max(parseInt(req.query.limit as string) || 50, 1), 200);
    const direction = (req.query.direction as string || 'both').toLowerCase();

    if (!label && !uri) {
      return res.status(400).json({ error: 'Either label or uri parameter is required' });
    }

    if (!['out', 'in', 'both'].includes(direction)) {
      return res.status(400).json({ error: 'direction must be one of: out, in, both' });
    }

    // Resolve entity
    const resolved = await resolveEntityInternal(label, uri, typeHint);

    if (!resolved) {
      return res.json({
        query_label: label,
        query_uri: uri,
        type_hint: typeHint,
        resolved_uri: null,
        resolved_entity_id: null,
        nodes: [],
        edges: [],
        truncated: false,
        error: 'Entity not found'
      });
    }

    // Build direction clause
    let directionClause = '';
    if (direction === 'out') {
      directionClause = 'AND r.subject_entity_id = $1';
    } else if (direction === 'in') {
      directionClause = 'AND r.object_entity_id = $1';
    } else {
      directionClause = 'AND (r.subject_entity_id = $1 OR r.object_entity_id = $1)';
    }

    // Query relationships with entity info
    const relQuery = `
      WITH edges AS (
        SELECT
          r.id as rel_id,
          r.predicate,
          r.confidence,
          r.occurrence_count as rel_occurrence_count,
          r.subject_entity_id,
          r.object_entity_id,
          CASE
            WHEN r.subject_entity_id = $1 THEN 'out'
            ELSE 'in'
          END as direction
        FROM koi_relationships r
        WHERE 1=1 ${directionClause}
        ORDER BY r.occurrence_count DESC, r.confidence DESC NULLS LAST
        LIMIT $2
      ),
      all_entity_ids AS (
        SELECT DISTINCT subject_entity_id as entity_id FROM edges
        UNION
        SELECT DISTINCT object_entity_id as entity_id FROM edges
      ),
      nodes AS (
        SELECT
          e.id,
          e.fuseki_uri as uri,
          e.entity_text as text,
          e.entity_type as type,
          e.occurrence_count,
          COALESCE(subj.subj_count, 0) + COALESCE(obj.obj_count, 0) as relationship_count
        FROM entity_registry e
        JOIN all_entity_ids a ON e.id = a.entity_id
        LEFT JOIN (
          SELECT subject_entity_id, COUNT(*) as subj_count
          FROM koi_relationships
          GROUP BY subject_entity_id
        ) subj ON e.id = subj.subject_entity_id
        LEFT JOIN (
          SELECT object_entity_id, COUNT(*) as obj_count
          FROM koi_relationships
          GROUP BY object_entity_id
        ) obj ON e.id = obj.object_entity_id
      )
      SELECT
        'edge' as result_type,
        ed.rel_id,
        ed.predicate,
        ed.confidence,
        ed.rel_occurrence_count,
        ed.direction,
        subj.fuseki_uri as subject_uri,
        obj.fuseki_uri as object_uri,
        NULL as node_id,
        NULL as node_uri,
        NULL as node_text,
        NULL as node_type,
        NULL as node_occurrence_count,
        NULL as node_relationship_count
      FROM edges ed
      JOIN entity_registry subj ON ed.subject_entity_id = subj.id
      JOIN entity_registry obj ON ed.object_entity_id = obj.id

      UNION ALL

      SELECT
        'node' as result_type,
        NULL as rel_id,
        NULL as predicate,
        NULL as confidence,
        NULL as rel_occurrence_count,
        NULL as direction,
        NULL as subject_uri,
        NULL as object_uri,
        n.id as node_id,
        n.uri as node_uri,
        n.text as node_text,
        n.type as node_type,
        n.occurrence_count as node_occurrence_count,
        n.relationship_count as node_relationship_count
      FROM nodes n
    `;

    const results = await pool.query(relQuery, [resolved.entity_id, limit]);

    // Separate nodes and edges
    const nodes: any[] = [];
    const edges: any[] = [];
    const nodeSet = new Set<number>();

    for (const row of results.rows) {
      if (row.result_type === 'node') {
        if (!nodeSet.has(row.node_id)) {
          nodeSet.add(row.node_id);
          nodes.push({
            id: row.node_id,
            uri: row.node_uri,
            text: row.node_text,
            type: row.node_type,
            occurrence_count: parseInt(row.node_occurrence_count),
            relationship_count: parseInt(row.node_relationship_count),
          });
        }
      } else if (row.result_type === 'edge') {
        edges.push({
          predicate: row.predicate,
          subject_uri: row.subject_uri,
          object_uri: row.object_uri,
          direction: row.direction,
          confidence: row.confidence,
          occurrence_count: parseInt(row.rel_occurrence_count),
        });
      }
    }

    // Check if results were truncated
    const truncated = edges.length >= limit;

    res.json({
      query_label: label,
      query_uri: uri,
      type_hint: typeHint,
      resolved_uri: resolved.uri,
      resolved_entity_id: resolved.entity_id,
      resolved_entity_text: resolved.entity_text,
      resolved_entity_type: resolved.entity_type,
      nodes: nodes,
      edges: edges,
      node_count: nodes.length,
      edge_count: edges.length,
      truncated: truncated,
    });

  } catch (error) {
    console.error('Neighborhood query error:', error);
    res.status(500).json({
      error: 'Neighborhood query failed',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Entity documents endpoint - find documents where an entity appears
// GET /api/koi/entity/documents?label=...&uri=...&type_hint=...&limit=20
app.get('/api/koi/entity/documents', async (req, res) => {
  try {
    const label = (req.query.label as string || '').trim() || null;
    const uri = (req.query.uri as string || '').trim() || null;
    const typeHint = (req.query.type_hint as string || '').trim().toUpperCase() || null;
    const limit = Math.min(Math.max(parseInt(req.query.limit as string) || 20, 1), 50);

    if (!label && !uri) {
      return res.status(400).json({ error: 'Either label or uri parameter is required' });
    }

    // Extract session token and validate for privacy filter
    const authHeader = req.headers['authorization'] as string | undefined;
    const sessionToken = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : undefined;
    const authenticatedEmail = await validateSessionToken(sessionToken);
    const isAuthenticated = !!authenticatedEmail;
    const privacyFilter = buildPrivacyFilter(isAuthenticated);

    // Resolve entity
    const resolved = await resolveEntityInternal(label, uri, typeHint);

    if (!resolved) {
      return res.json({
        query_label: label,
        query_uri: uri,
        type_hint: typeHint,
        resolved_uri: null,
        resolved_entity_id: null,
        documents: [],
        error: 'Entity not found'
      });
    }

    // Query documents via koi_entity_chunk_links
    // Join on document_rid to match koi_memories.rid
    const docQuery = `
      WITH entity_docs AS (
        SELECT DISTINCT
          l.document_rid,
          l.entity_name,
          MAX(l.confidence) as link_confidence
        FROM koi_entity_chunk_links l
        WHERE l.entity_uri = $1
           OR LOWER(TRIM(l.entity_name)) = LOWER(TRIM($2))
        GROUP BY l.document_rid, l.entity_name
        LIMIT $3 * 3
      ),
      docs_with_content AS (
        SELECT
          ed.document_rid,
          ed.entity_name,
          ed.link_confidence,
          m.content->>'text' as snippet,
          m.metadata->>'source' as source,
          m.metadata->>'url' as url,
          m.published_at,
          m.rid
        FROM entity_docs ed
        JOIN koi_memories m ON m.rid = ed.document_rid
        WHERE m.superseded_at IS NULL
          AND m.content->>'text' IS NOT NULL
          ${privacyFilter}
      ),
      deduplicated AS (
        SELECT *,
          ROW_NUMBER() OVER (
            PARTITION BY md5(snippet)
            ORDER BY published_at DESC NULLS LAST
          ) as content_rank
        FROM docs_with_content
      )
      SELECT
        rid,
        document_rid,
        url,
        source,
        SUBSTRING(snippet, 1, 500) as snippet,
        published_at,
        entity_name,
        link_confidence
      FROM deduplicated
      WHERE content_rank = 1
      ORDER BY published_at DESC NULLS LAST, link_confidence DESC NULLS LAST
      LIMIT $3
    `;

    const normalizedLabel = label || resolved.entity_text;
    const results = await pool.query(docQuery, [resolved.uri, normalizedLabel, limit]);

    const documents = results.rows.map(row => ({
      rid: row.rid,
      document_rid: row.document_rid,
      url: row.url,
      source: row.source,
      snippet: row.snippet,
      published_at: row.published_at,
      entity_matched: row.entity_name,
      confidence: row.link_confidence,
    }));

    res.json({
      query_label: label,
      query_uri: uri,
      type_hint: typeHint,
      resolved_uri: resolved.uri,
      resolved_entity_id: resolved.entity_id,
      resolved_entity_text: resolved.entity_text,
      resolved_entity_type: resolved.entity_type,
      document_count: documents.length,
      documents: documents,
    });

  } catch (error) {
    console.error('Entity documents query error:', error);
    res.status(500).json({
      error: 'Entity documents query failed',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

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

// Statistics endpoint
app.get('/api/koi/stats', async (req, res) => {
  try {
    // Total documents
    const totalResult = await pool.query('SELECT COUNT(*) as total FROM koi_memories');
    const total = parseInt(totalResult.rows[0].total);

    // By source
    const bySourceResult = await pool.query(`
      SELECT
        metadata->>'source' as source,
        COUNT(*) as count
      FROM koi_memories
      WHERE metadata->>'source' IS NOT NULL
      GROUP BY source
      ORDER BY count DESC
    `);

    // Recent activity (last 7 days)
    const recentResult = await pool.query(`
      SELECT COUNT(*) as recent
      FROM koi_memories
      WHERE created_at > NOW() - INTERVAL '7 days'
    `);
    const recent = parseInt(recentResult.rows[0].recent);

    // Format by_source as object
    const by_source = {};
    bySourceResult.rows.forEach(row => {
      by_source[row.source] = parseInt(row.count);
    });

    res.json({
      total_documents: total,
      recent_7_days: recent,
      by_source: by_source
    });
  } catch (error) {
    console.error('Stats error:', error);
    res.status(500).json({
      error: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});
// Weekly digest endpoint - simplified version using search
app.get('/api/koi/weekly-digest', async (req, res) => {
  try {
    const { start_date, end_date, format = 'markdown' } = req.query;

    // Calculate dates
    const now = new Date();
    const endDateStr = end_date || now.toISOString().split('T')[0];
    
    let startDateStr = start_date;
    if (!startDateStr) {
      const weekAgo = new Date(now);
      weekAgo.setDate(now.getDate() - 7);
      startDateStr = weekAgo.toISOString().split('T')[0];
    }

    console.log(`Generating weekly digest from ${startDateStr} to ${endDateStr}`);

    // Query for recent Regen Network activity
    const searchQuery = 'Regen Network activity updates discussions governance proposals';
    const topK = 50;

    // Perform semantic search with date filters
    const bgeResponse = await fetch('http://localhost:8090/encode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: searchQuery })
    });

    let results = [];
    
    if (bgeResponse.ok) {
      const { embedding } = await bgeResponse.json();
      
      // Vector search with date range - use parameterized query
      const vectorQuery = `
        SELECT
          m.rid,
          m.content->>'text' as content,
          m.metadata->>'source' as source,
          m.metadata->>'url' as url,
          1 - (e.dim_1024 <=> $1::vector) as similarity,
          m.published_at
        FROM koi_memories m
        JOIN koi_embeddings e ON m.id = e.memory_id
        WHERE
          m.content->>'text' IS NOT NULL
          AND LENGTH(m.content->>'text') > 50
        AND m.superseded_at IS NULL
          AND e.dim_1024 IS NOT NULL
          AND m.published_at >= $2::timestamptz
          AND m.published_at <= $3::timestamptz
        ORDER BY e.dim_1024 <=> $1::vector
        LIMIT $4
      `;
      
      const queryResults = await pool.query(vectorQuery, [
        JSON.stringify(embedding),
        startDateStr,
        endDateStr,
        topK
      ]);
      
      results = queryResults.rows;
    } else {
      // Fallback to keyword search
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
        AND m.superseded_at IS NULL
          AND m.published_at >= $1::timestamptz
          AND m.published_at <= $2::timestamptz
        ORDER BY m.published_at DESC
        LIMIT $3
      `;
      
      const queryResults = await pool.query(fallbackQuery, [startDateStr, endDateStr, topK]);
      results = queryResults.rows;
    }

    // Generate markdown digest
    let markdownContent = `# Regen Network Weekly Digest\\n\\n`;
    markdownContent += `**Period:** ${startDateStr} to ${endDateStr}\\n\\n`;
    markdownContent += `## Summary\\n\\n`;
    markdownContent += `This digest contains ${results.length} recent documents and discussions from the Regen Network community.\\n\\n`;
    markdownContent += `## Recent Activity\\n\\n`;

    // Group by date
    const byDate = {};
    results.forEach(r => {
      const date = r.published_at ? r.published_at.toISOString().split('T')[0] : 'undated';
      if (!byDate[date]) byDate[date] = [];
      byDate[date].push(r);
    });

    // Output results by date
    Object.keys(byDate).sort().reverse().forEach(date => {
      if (date !== 'undated') {
        markdownContent += `### ${date}\\n\\n`;
      }
      byDate[date].forEach(item => {
        const preview = item.content.substring(0, 200);
        markdownContent += `**Source:** ${item.source}\\n`;
        if (item.url) {
          markdownContent += `**URL:** ${item.url}\\n`;
        }
        markdownContent += `${preview}...\\n\\n---\\n\\n`;
      });
    });

    const wordCount = markdownContent.split(/\\s+/).length;

    if (format === 'json') {
      res.json({
        success: true,
        week_start: startDateStr,
        week_end: endDateStr,
        total_items: results.length,
        content: markdownContent,
        metadata: {
          word_count: wordCount,
          source_count: results.length
        }
      });
    } else {
      res.json({
        success: true,
        format: 'markdown',
        content: markdownContent,
        metadata: {
          week_start: startDateStr,
          week_end: endDateStr,
          total_items: results.length,
          word_count: wordCount
        }
      });
    }
  } catch (error) {
    console.error('Weekly digest error:', error);
    res.status(500).json({
      success: false,
      error: error.message || 'Failed to generate weekly digest'
    });
  }
});

// Debug ping endpoint
app.get('/api/koi/ping', (req, res) => {
  console.log('[Ping] Request received');
  res.json({ pong: true, version: 3, time: new Date().toISOString() });
});

// Debug endpoint for entity search testing
app.get('/api/koi/debug-entity', async (req, res) => {
  const query = (req.query.q as string) || "What is the Regen Network?";
  console.log(`[Debug] Testing entity search for: "${query}"`);

  try {
    const entityResults = await performEntitySearch(query, 10, '');
    res.json({
      query,
      entity_count: entityResults.length,
      entities: entityResults.slice(0, 5).map((r: any) => ({
        rid: r.rid?.substring(0, 50),
        score: r.score,
        entities_matched: r.metadata?.entities_matched
      }))
    });
  } catch (err: any) {
    console.error('[Debug] Error:', err);
    res.status(500).json({ error: err.message, stack: err.stack });
  }
});

console.log('[Init] Debug endpoints registered');

app.listen(PORT, () => {
  console.log(`🚀 KOI Query API running on http://localhost:${PORT}`);
  console.log(`📊 Health check: http://localhost:${PORT}/api/koi/health`);
  console.log(`🔍 Query endpoint: POST http://localhost:${PORT}/api/koi/query`);
  console.log(`🔧 Debug: GET http://localhost:${PORT}/api/koi/ping`);
});

export default app;
