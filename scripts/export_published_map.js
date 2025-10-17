#!/usr/bin/env node
/**
 * Export a simple published_at mapping to JSON for refine_graph.py enrichment.
 * Keys are derived from memory metadata/title/url for substring matching.
 *
 * Env:
 *  - POSTGRES_URL (e.g., postgresql://user:pass@host:port/db)
 *  - OUTPUT_PATH (default: src/core/published_map.json)
 *  - LIMIT (optional, limit rows for testing)
 */

import { writeFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { Client } from 'pg';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function parsePostgresUrl(url) {
  const m = url.match(/postgres(?:ql)?:\/\/([^:]+):([^@]+)@([^:]+):(\d+)\/(.+)/);
  if (!m) throw new Error('Invalid POSTGRES_URL');
  return { user: m[1], password: m[2], host: m[3], port: parseInt(m[4], 10), database: m[5] };
}

async function main() {
  const POSTGRES_URL = process.env.POSTGRES_URL || 'postgresql://postgres:postgres@localhost:5433/eliza';
  const OUT = process.env.OUTPUT_PATH || resolve(__dirname, '../src/core/published_map.json');
  const LIMIT = process.env.LIMIT ? parseInt(process.env.LIMIT, 10) : null;

  const cfg = parsePostgresUrl(POSTGRES_URL);
  const client = new Client(cfg);
  await client.connect();

  const rows = (await client.query(
    `SELECT rid,
            COALESCE(m.metadata->>'title', m.metadata->>'url') AS key,
            m.metadata->>'title' AS title,
            m.metadata->>'url' AS url,
            m.published_at::timestamptz AS published_at
     FROM koi_memories m
     WHERE m.published_at IS NOT NULL
     ${LIMIT ? 'LIMIT ' + LIMIT : ''}`
  )).rows;

  const out = {};
  for (const r of rows) {
    const ts = r.published_at ? new Date(r.published_at).toISOString() : null;
    if (!ts) continue;
    // Prefer title key
    if (r.title && r.title.length >= 6) out[r.title] = ts;
    // Also include URL as key
    if (r.url && r.url.length >= 10) out[r.url] = ts;
    // Include last path segment of URL as a heuristic key
    if (r.url) {
      try {
        const u = new URL(r.url);
        const parts = u.pathname.split('/').filter(Boolean);
        if (parts.length > 0) {
          const last = decodeURIComponent(parts[parts.length - 1]);
          if (last && last.length >= 4) out[last] = ts;
        }
      } catch {}
    }
  }

  // Write file
  writeFileSync(OUT, JSON.stringify(out, null, 2));
  console.log(`Wrote ${Object.keys(out).length} mapping entries to ${OUT}`);

  await client.end();
}

main().catch((e) => {
  console.error('export_published_map failed:', e);
  process.exit(1);
});

