<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-23 00:10 PDT

**Current status:** All executable priorities done and pushed (`38c11fe`). Meeting graph 302 entities / 1,261 edges, ZERO cross-date groups and ZERO date-mismatched edges. Two clock-gated items remain plus one design decision.

**Next:** 1) koi 7878 — evaluate no earlier than 2026-08-23 13:47 PDT; currently 0 nonconforming. 2) DECIDE: make replay the primary evidence for the resolver shadow gate — at 10% sampling and ~60 organic entity creations/day it needs ~170 days to reach its 1,000-attempt bar, and 0 have been emitted. 3) Migration 112 eligible after 2026-08-29 10:05 PDT; keep `Organization` a distinct core type.

**Watch:** The 3 residual excess Meeting mappings are INTENTIONAL (same meeting, same date, multiple artifacts) — do not drive them to zero. Never de-dup Person rows naively; the `dave` alias would misroute 20 of 22 "Dave" attendees away from David Fortson. Re-measure before acting; concurrent sessions write this DB.

**Verification:** regen-prod, 0 uncommitted, 0 ahead. Red-baseline gate 10/10 PASS; live-write governance 4/4; 78 focused tests pass. API healthy. Every delete has a timestamped backup table.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
