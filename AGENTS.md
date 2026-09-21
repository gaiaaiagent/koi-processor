<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-20 (third fix round, session b35cb9db)

**Current status:** `fix/federated-fact-retractions` — third (bounded) fix round applied 2026-09-20 on re-reviewed head `c41fa77`: two commits (code+tests, docs/handoff; `git log --oneline 0c39fa1..HEAD` is the ledger for SHAs and count), pushed. Fixed and revert-proven: total unsigned-poll predicate (vault-sync events no longer dropped for unsigned pollers), state-qualified sweep mutations (a racing confirm/report is never overwritten), `rejected: peer_holds_different_valid_to` listed under terminal failures, observer-path `failed` logged, `--scope-enforced-since auto` bound to the SERVING process on a local DSN (explicit ISO preferred), real signed-confirm seam test for `pending`. **Draft PR #70 stays draft; #67 stays OPEN.** Nothing deployed; 126/127 NOT applied live; no restart; no peer contacted; no live writes.

**Next:** 1) Re-review at the new head if wanted. 2) Deploy per `docs/federation/fact-retractions.md` §4.1: step 0 — deploying this branch deploys PR #66 code; `KOI_ENFORCE_TARGET_MATCH=true`; envelope replay/freshness is an UNRESOLVED deployment blocker (signing does not solve it) → 126 + 127 on publisher → code to BOTH checkouts → restart → `ps -o lstart=` → `KOI_FACT_RETRACTION_SWEEP=true` → general-stream signed gate → NUC by hand (§4.1a) → canary. 3) #67 leftovers unchanged: two un-obligated scripts, repair application, NUC proof, five `koi-67-*` tasks + the unregistered `koi-67-envelope-replay-protection`. 4) #69.

**Watch:** audit numbers unchanged (3,847 possibly_live + 196 unverifiable on nuc-personal; 729 `scope_excluded` only with a floor; use the explicit instant `2026-08-26T02:50:25+00:00`, a lower bound). `personal_koi_test` carries an unmerged migration 119 edge CHECK; tests drop it in-transaction. The full suite's passed total moves ±1 with the launchd jobs running at collection (`test_launchd_job_targets.py` parametrizes over them) — compare FAILED/ERROR id sets, not totals.

**Verification:** tree clean, HEAD == origin; six #67 files `--collect-only` = **179** (7/67/28/3/20/54), all green; 13 new test functions, 12 red at `c41fa77` (the seam test pins behaviour that already worked), 5 targeted mutants each red; full suite vs `0c39fa1` with identical flags: base 54F/10E/1,831 passed, branch 54F/10E/2,011 passed (+180 = 179 six-file tests + 1 launchd-parametrized case), FAILED/ERROR node-id sets identical (64 = 64); tripwire OK.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
