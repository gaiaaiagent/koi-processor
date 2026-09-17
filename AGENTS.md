<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-17 09:05 PDT

**Current status:** `fix/federated-fact-retractions` — second review round applied 2026-09-17 (code `433b167` + a docs commit; read `git rev-parse HEAD`), pushed. **Draft PR #70** re-worded (no auto-close of #67), `pending` delivery state, `received` never failed by edge narrowing, signed-only retraction transport (fail-closed regardless of env), audit floor explicit/verified, terminal failures outstanding + logged, 127 strict shape assertion. **#67 stays OPEN** (AC table re-posted; 5 deferred findings tracked as `koi-67-*` tasks). 127 **NOT applied**; nothing deployed; no peer contacted; no live writes.

**Next:** 1) Re-review PR #70 at the new head. 2) Deploy order (PR body): 127 on publisher AND recipients → code to BOTH checkouts → restart → `ps -o lstart=` → `KOI_FACT_RETRACTION_SWEEP=true` → gate `koi-67-signed-envelopes-general-stream` → NUC by hand → canary. 3) #67 leftovers: two un-obligated scripts + repair application. 4) #69 rollback/replay (replay task due 09-21).

**Watch:** audit (2026-09-17, `--scope-enforced-since auto`): **3,847 facts probably still live on nuc-personal** (+196 unverifiable); the NUC gets no code automatically and its old code still resurrects via UPDATE-before-NEW. Without the floor flag the 729 `scope_excluded` classify `unauthorized`. `personal_koi_test` carries an unmerged migration 119 edge CHECK; tests drop it in-transaction.

**Verification:** tree clean, HEAD == origin; 155 tests in the six #67 files green (19 new, red-first; 7 revert-proof mutants red); 127 real up/up/down on scratch exit 0/0/0, post-state == pre-state; full suite vs `0c39fa1`, same flags: base **54F / 10E / 1,832 passed**, branch **54F / 10E / 1,987 passed** (+155 = the six #67 files); FAILED/ERROR node-id sets **identical** (64 = 64, no id only on either side); tripwire OK.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
