-- Migration 083: MediaWiki redirect resolver function + view + supporting indexes
--
-- Adds infrastructure for transparently resolving redirect titles to their
-- canonical page_state row. First consumer: scripts/mediawiki_review.py
-- (inspect + promote). Semantic search (unified_search) already works for
-- redirect-title queries; this migration serves title-based lookup paths.
--
-- Purely additive: CREATE OR REPLACE FUNCTION, DROP VIEW + CREATE VIEW,
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS. No ALTER on existing tables.
-- Safe to re-run (idempotent).
--
-- IMPORTANT: This file must NOT be wrapped in BEGIN/COMMIT — CREATE INDEX
-- CONCURRENTLY cannot run inside a transaction block. psql -f executes each
-- top-level statement in its own implicit transaction, which is compatible.

\set ON_ERROR_STOP on

-- Guards: wrong-database + wrong-role (fail-closed via RAISE EXCEPTION).
-- PL/pgSQL DO block is used because PG constant-folds 1/0 in SQL CASE
-- expressions at plan time, defeating the guard when the WHEN clause
-- contains a function call like current_database().
DO $$
BEGIN
    IF current_database() NOT LIKE 'personal_koi%' THEN
        RAISE EXCEPTION 'Wrong database: % (expected personal_koi*)', current_database();
    END IF;
    IF current_user <> 'darrenzal' THEN
        RAISE EXCEPTION 'Wrong user: % (expected darrenzal)', current_user;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Function: resolve a redirect title to its canonical page_state.id
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION mediawiki_resolve_redirect(p_title TEXT, p_wiki_id INT)
RETURNS BIGINT
LANGUAGE sql STABLE
AS $$
    WITH RECURSIVE chain(id, title, redirect_target, is_redirect, depth, visited) AS (
        -- Base case: find the page matching the input title
        -- Subquery for tie-break (ORDER BY/LIMIT not allowed in UNION ALL legs)
        SELECT b.id, b.title, b.redirect_target, b.is_redirect, 0, ARRAY[b.title]
        FROM (
            SELECT ps.id, ps.title, ps.redirect_target, ps.is_redirect
            FROM mediawiki_page_state ps
            WHERE ps.title = p_title AND ps.wiki_id = p_wiki_id
            ORDER BY ps.id DESC
            LIMIT 1
        ) b

        UNION ALL

        -- Recursive case: follow redirect_target (strip #section and |label)
        SELECT nxt.id, nxt.title, nxt.redirect_target, nxt.is_redirect,
               c.depth + 1,
               c.visited || nxt.title
        FROM chain c
        JOIN LATERAL (
            SELECT ps.id, ps.title, ps.redirect_target, ps.is_redirect
            FROM mediawiki_page_state ps
            WHERE ps.title = split_part(split_part(c.redirect_target, '#', 1), '|', 1)
              AND ps.wiki_id = p_wiki_id
            ORDER BY ps.id DESC
            LIMIT 1
        ) nxt ON true
        WHERE c.is_redirect = true
          AND c.depth < 5
          AND NOT (nxt.title = ANY(c.visited))  -- cycle detection
    )
    SELECT id FROM chain
    WHERE is_redirect = false  -- found the canonical (non-redirect) page
    ORDER BY depth ASC
    LIMIT 1
$$;

-- ---------------------------------------------------------------------------
-- Helper function: returns (canonical_id, hops, resolution_status) for a
-- single page_state row. The view calls this per-row via LATERAL.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION mediawiki_resolve_redirect_info(
    p_title TEXT, p_wiki_id INT, p_is_redirect BOOLEAN, p_self_id BIGINT
)
RETURNS TABLE(canonical_id BIGINT, hops INT, resolution_status TEXT)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    cur_title TEXT;
    cur_target TEXT;
    cur_is_redirect BOOLEAN;
    cur_id BIGINT;
    visited TEXT[];
    depth INT := 0;
BEGIN
    -- Non-redirects resolve to self immediately
    IF NOT p_is_redirect THEN
        RETURN QUERY SELECT p_self_id, 0, NULL::TEXT;
        RETURN;
    END IF;

    cur_title := p_title;
    visited := ARRAY[cur_title];

    -- Walk the chain
    LOOP
        -- Get current row's redirect_target
        SELECT ps.redirect_target INTO cur_target
        FROM mediawiki_page_state ps
        WHERE ps.title = cur_title AND ps.wiki_id = p_wiki_id
        ORDER BY ps.id DESC LIMIT 1;

        IF cur_target IS NULL THEN
            -- Should not happen (we started from a redirect), but guard
            RETURN QUERY SELECT NULL::BIGINT, NULL::INT, 'missing_target'::TEXT;
            RETURN;
        END IF;

        -- Strip #section and |label
        cur_target := split_part(split_part(cur_target, '#', 1), '|', 1);

        -- Look up the target
        SELECT ps.id, ps.title, ps.is_redirect
        INTO cur_id, cur_title, cur_is_redirect
        FROM mediawiki_page_state ps
        WHERE ps.title = cur_target AND ps.wiki_id = p_wiki_id
        ORDER BY ps.id DESC LIMIT 1;

        depth := depth + 1;

        IF cur_id IS NULL THEN
            RETURN QUERY SELECT NULL::BIGINT, NULL::INT, 'missing_target'::TEXT;
            RETURN;
        END IF;

        IF NOT cur_is_redirect THEN
            -- Found canonical page
            RETURN QUERY SELECT cur_id, depth, 'resolved'::TEXT;
            RETURN;
        END IF;

        IF cur_title = ANY(visited) THEN
            RETURN QUERY SELECT NULL::BIGINT, NULL::INT, 'cycle'::TEXT;
            RETURN;
        END IF;

        IF depth >= 5 THEN
            RETURN QUERY SELECT NULL::BIGINT, NULL::INT, 'depth_exceeded'::TEXT;
            RETURN;
        END IF;

        visited := visited || cur_title;
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- View: every page_state row enriched with canonical resolution info
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS v_mediawiki_page_resolved;

CREATE VIEW v_mediawiki_page_resolved AS
SELECT
    ps.id,
    ps.wiki_id,
    ps.title,
    ps.is_redirect,
    ps.redirect_target,
    info.canonical_id,
    -- Derive canonical_page_id, canonical_title, canonical_rid from canonical_id
    canon.page_id AS canonical_page_id,
    canon.title AS canonical_title,
    CASE WHEN info.canonical_id IS NOT NULL
         THEN 'mediawiki:' || w.wiki_name || ':' || canon.page_id
         ELSE NULL
    END AS canonical_rid,
    info.hops,
    info.resolution_status
FROM mediawiki_page_state ps
JOIN mediawiki_wikis w ON w.id = ps.wiki_id
LEFT JOIN LATERAL mediawiki_resolve_redirect_info(
    ps.title, ps.wiki_id, ps.is_redirect, ps.id
) info ON true
LEFT JOIN mediawiki_page_state canon ON canon.id = info.canonical_id;

-- ---------------------------------------------------------------------------
-- Supporting indexes (CONCURRENTLY — no write blocking)
-- ---------------------------------------------------------------------------

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mw_page_wiki_title
    ON mediawiki_page_state (wiki_id, title);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mw_page_wiki_redirect_target
    ON mediawiki_page_state (wiki_id, redirect_target)
    WHERE is_redirect = true;
