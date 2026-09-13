<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-12 22:40 PDT

**Current status:** `regen-prod` @ `60d7414`, 2 ahead (unpushed). **Migration 123 is APPLIED and live** (`claims=513, snapshot rows=511`; one-shot, do not re-run). Stream A plan steps 0–3 done; steps 4–5 (ingest edits + history router) not started.

**Next:** 1. Step 4 — `scripts/ingest_document.py` per `…-artifacts/q1_fixed_pair.md` (canonical), fixtures on a scratch DB. 2. Step 5 — `api/routers/history_router.py` + apply txn (degraded = 503, never a mistakable 200), then the checkpoint (task `koi-2026-09-13-045186e8-step5-checkpoint`): show behaviour when the write succeeds but the bytes are wrong. 3. Push; verify with `git ls-remote`.

**Watch:** Nothing blocks. Fixtures `656d1923`/`c3b0ebcd`/`58fe10e0` are DO-NOT-MODIFY. `drop` policy stays disabled in v1. Gate heavy work on `aomhost` absent + swap flat, never `pgrep zoom.us`/`pages free`. Use `/usr/bin/stat`.

**Verification:** `git diff --check` clean; post-apply anti-joins 0/509 and 0; nine indexes present; fresh dump full-read verified. `p.txt`/`:` are other sessions' debris.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
