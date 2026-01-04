#!/usr/bin/env bun
/**
 * Backfill Discourse post authors into koi_memories.metadata
 *
 * Why:
 * - Older Discourse content ingested via the semantic bridge did not persist author metadata.
 * - person_activity intent relies on authored-evidence for forum posts (mention ≠ evidence).
 *
 * This script:
 * - Finds recent Discourse post memories missing author fields
 * - Fetches author info from Discourse (allowlisted) via /posts/by_number/{topic_id}/{post_number}.json
 * - Writes metadata.author, metadata.author_username, metadata.author_name, metadata.author_id
 *
 * Usage:
 *   POSTGRES_URL=... bun run scripts/backfill-discourse-authors.ts
 *
 * Options (env):
 *   MONTHS_BACK=24          # only consider posts within N months by published_at (default 24)
 *   LIMIT=200               # max rows to process in this run (default 200)
 *   OFFSET=0                # offset for paging through candidates (default 0)
 *   CONCURRENCY=4           # concurrent HTTP fetches (default 4)
 *   DRY_RUN=1               # don't write DB updates
 *   ALLOWED_DISCOURSE_HOSTS=forum.regen.network,regencommons.discourse.group
 */

import { Pool } from 'pg';

// =============================================================================
// Config
// =============================================================================

const POSTGRES_URL = process.env.POSTGRES_URL || 'postgresql://postgres:postgres@localhost:5433/eliza';
const MONTHS_BACK = Number.parseInt(process.env.MONTHS_BACK || '24', 10);
const LIMIT = Number.parseInt(process.env.LIMIT || '200', 10);
const OFFSET = Number.parseInt(process.env.OFFSET || '0', 10);
const CONCURRENCY = Math.max(1, Number.parseInt(process.env.CONCURRENCY || '4', 10));
const DRY_RUN = process.env.DRY_RUN === '1' || process.env.DRY_RUN === 'true';
const ALLOWED_DISCOURSE_HOSTS = new Set(
  (process.env.ALLOWED_DISCOURSE_HOSTS || 'forum.regen.network,regencommons.discourse.group')
    .split(',')
    .map(s => s.trim().toLowerCase())
    .filter(Boolean)
);

// =============================================================================
// Helpers
// =============================================================================

type CandidateRow = {
  rid: string;
  url: string | null;
  published_at: string | null;
  author: string | null;
  author_username: string | null;
  author_name: string | null;
  author_id: string | null;
};

type RidParsed = {
  host: string;
  topicId: number;
  postNumber: number;
};

function parseForumPostRid(rid: string): RidParsed | null {
  // Example: regen.forum-post:forum.regen.network_565_post_3
  const match = rid.match(/^regen\.forum-post:([^_]+)_(\d+)_post_(\d+)(?:$|[#_])/);
  if (!match) return null;
  const host = match[1].trim().toLowerCase();
  const topicId = Number.parseInt(match[2], 10);
  const postNumber = Number.parseInt(match[3], 10);
  if (!host || !Number.isFinite(topicId) || !Number.isFinite(postNumber)) return null;
  return { host, topicId, postNumber };
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function parseRetryAfterMs(resp: Response): number | null {
  const raw = resp.headers.get('retry-after');
  if (!raw) return null;
  const seconds = Number.parseInt(raw, 10);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  return null;
}

async function fetchWithRetries(url: string, maxAttempts: number = 4): Promise<Response> {
  let attempt = 0;
  while (true) {
    attempt++;
    const resp = await fetch(url, {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'koi-processor/backfill-discourse-authors',
      },
    });

    if (resp.status !== 429 && resp.status < 500) {
      return resp;
    }

    if (attempt >= maxAttempts) {
      return resp;
    }

    const retryAfter = parseRetryAfterMs(resp);
    const backoff = retryAfter ?? Math.floor((250 * Math.pow(2, attempt - 1)) + Math.random() * 250);
    await sleep(Math.min(backoff, 5000));
  }
}

async function resolveAuthorFromDiscourse(host: string, topicId: number, postNumber: number): Promise<{
  author_username: string | null;
  author_name: string | null;
  author_id: number | null;
  post_id: number | null;
  post_url: string | null;
}> {
  const url = `https://${host}/posts/by_number/${topicId}/${postNumber}.json`;
  const resp = await fetchWithRetries(url);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} for ${url}`);
  }
  const data: any = await resp.json();

  const author_username = typeof data?.username === 'string' ? data.username : null;
  const author_name = typeof data?.name === 'string' ? data.name : null;
  const author_id = typeof data?.user_id === 'number' ? data.user_id : null;
  const post_id = typeof data?.id === 'number' ? data.id : null;
  const post_url = typeof data?.post_url === 'string' ? data.post_url : null;

  return { author_username, author_name, author_id, post_id, post_url };
}

function buildMetadataPatch(resolved: {
  author_username: string | null;
  author_name: string | null;
  author_id: number | null;
}): Record<string, any> {
  const patch: Record<string, any> = {};
  const author = resolved.author_name || resolved.author_username;
  if (author) patch.author = author;
  if (resolved.author_username) patch.author_username = resolved.author_username;
  if (resolved.author_name) patch.author_name = resolved.author_name;
  if (typeof resolved.author_id === 'number') patch.author_id = resolved.author_id;
  return patch;
}

async function runWithConcurrency<T>(
  items: T[],
  concurrency: number,
  worker: (item: T, idx: number) => Promise<void>
): Promise<void> {
  let index = 0;
  const workers = new Array(Math.min(concurrency, items.length)).fill(null).map(async () => {
    while (true) {
      const i = index++;
      if (i >= items.length) return;
      await worker(items[i], i);
    }
  });
  await Promise.all(workers);
}

// =============================================================================
// Main
// =============================================================================

async function main() {
  console.log('='.repeat(80));
  console.log('Backfill Discourse Authors → koi_memories.metadata');
  console.log('='.repeat(80));
  console.log(`POSTGRES_URL: ${POSTGRES_URL.replace(/:\/\/[^@]+@/, '://***@')}`);
  console.log(`MONTHS_BACK: ${MONTHS_BACK}`);
  console.log(`LIMIT: ${LIMIT}`);
  console.log(`OFFSET: ${OFFSET}`);
  console.log(`CONCURRENCY: ${CONCURRENCY}`);
  console.log(`DRY_RUN: ${DRY_RUN}`);
  console.log(`ALLOWED_DISCOURSE_HOSTS: ${Array.from(ALLOWED_DISCOURSE_HOSTS).join(', ')}`);
  console.log('');

  const pool = new Pool({ connectionString: POSTGRES_URL });
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - Math.max(0, MONTHS_BACK));

  try {
    const candidatesResult = await pool.query<CandidateRow>(
      `
      SELECT
        m.rid,
        m.metadata->>'url' AS url,
        m.published_at::text AS published_at,
        m.metadata->>'author' AS author,
        m.metadata->>'author_username' AS author_username,
        m.metadata->>'author_name' AS author_name,
        m.metadata->>'author_id' AS author_id
      FROM koi_memories m
      WHERE m.superseded_at IS NULL
        AND m.rid LIKE 'regen.forum-post:%'
        AND m.published_at IS NOT NULL
        AND m.published_at >= $1::timestamptz
        AND COALESCE(m.metadata->>'author', '') = ''
        AND COALESCE(m.metadata->>'author_username', '') = ''
      ORDER BY m.published_at DESC
      LIMIT $2 OFFSET $3
      `,
      [cutoff.toISOString(), LIMIT, OFFSET]
    );

    const rows = candidatesResult.rows;
    console.log(`Found ${rows.length} candidate(s) missing author metadata.\n`);

    let updated = 0;
    let skipped = 0;
    let failed = 0;

    await runWithConcurrency(rows, CONCURRENCY, async (row, idx) => {
      const parsed = parseForumPostRid(row.rid);
      if (!parsed) {
        skipped++;
        console.log(`[skip] ${row.rid} (unparseable rid)`);
        return;
      }

      if (!ALLOWED_DISCOURSE_HOSTS.has(parsed.host)) {
        skipped++;
        console.log(`[skip] ${row.rid} (host not allowlisted: ${parsed.host})`);
        return;
      }

      try {
        const resolved = await resolveAuthorFromDiscourse(parsed.host, parsed.topicId, parsed.postNumber);
        const patch = buildMetadataPatch(resolved);
        if (Object.keys(patch).length === 0) {
          skipped++;
          console.log(`[skip] ${row.rid} (no author fields resolved)`);
          return;
        }

        if (DRY_RUN) {
          updated++;
          console.log(`[dry-run] ${row.rid} ← ${JSON.stringify(patch)}`);
          return;
        }

        const res = await pool.query(
          `
          UPDATE koi_memories
          SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb
          WHERE rid = $1 AND superseded_at IS NULL
          `,
          [row.rid, JSON.stringify(patch)]
        );

        if (res.rowCount === 1) {
          updated++;
          if (idx % 25 === 0) {
            console.log(`[ok] ${row.rid} ← ${patch.author_username || patch.author}`);
          }
        } else {
          failed++;
          console.log(`[fail] ${row.rid} (rowCount=${res.rowCount})`);
        }
      } catch (err: any) {
        failed++;
        console.log(`[fail] ${row.rid} (${err?.message || err})`);
      }
    });

    console.log('\n' + '-'.repeat(80));
    console.log(`Done. updated=${updated}, skipped=${skipped}, failed=${failed}`);
    console.log('-'.repeat(80));
  } finally {
    await pool.end();
  }
}

main().catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
