<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-16 11:05 PDT

**Current status:** `fix/federated-fact-retractions` clean and pushed at `e362178` (5 commits over `0c39fa1` = `regen-prod` after PR #66 merged). **Draft PR #70** = #67's durable-retraction foundation (one-transaction retraction, unicast per admitting edge, migration 127 ledger, signed application reports, `LEAST` upsert, pending tombstones, lookups, opt-in sweep, read-only audit). **#67 stays OPEN** (AC table posted). 127 **NOT applied**; nothing deployed; no peer contacted; no live writes.

**Next:** 1) Review PR #70; deploy in order: 127 on publisher AND recipients → code to BOTH checkouts → restart → `ps -o lstart=`. 2) #67 leftovers: two un-obligated scripts (`extract_deep_documents.py` dedup, `ingest_research_papers.py` invalid-fact retire) + repair application for the 4,043-line plan. 3) #69 rollback/replay (replay task due 09-21 gated by it).

**Watch:** audit: **3,847 facts probably still live on nuc-personal** (+196 unverifiable); the NUC gets no code automatically and its old code still resurrects via UPDATE-before-NEW. `personal_koi_test` carries an unmerged migration 119 edge CHECK; tests drop it in-transaction.

**Verification:** tree clean, HEAD == origin; 136 new tests green; full suite vs `0c39fa1`, same flags: 54F/10E both sides, failure sets identical, +138 passing; tripwire OK; 20-finding review applied.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
