<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-12 22:55 PDT

**Current status:** `regen-prod` @ `e1cfac8`, in sync with origin; migration 123 APPLIED and live (`claims=513, snapshot rows=511`, one-shot — never re-run); every database + the source archive have encrypted, checksum-verified off-host copies on gaia, recovery proven on the NUC; Stream A steps 0–3 done, 4–5 not started.

**Next:** 1) Step 4 — `scripts/ingest_document.py` per artifacts `q1_fixed_pair.md` (CANONICAL), fixtures on a scratch DB. 2) Step 5 — `api/routers/history_router.py` + apply txn; then CHECKPOINT (task `koi-2026-09-13-045186e8-step5-checkpoint`, due 09-14): show behaviour when the write SUCCEEDS but the bytes are WRONG. 3) Stream B follow-ups are dated koi tasks in the :5051 Upcoming view.

**Watch:** Nothing blocks 4–5. Fixtures `656d1923`/`c3b0ebcd`/`58fe10e0` — DO NOT MODIFY. Heavy-work gate: `aomhost` absent, 1m load < 15m, swap flat; never `pgrep zoom.us`/`pages free`. Re-measure at the moment of the run.

**Verification:** `git diff --check` clean; no canon validator. `koi_backup_check.sh` all fresh. Off-host is checksum- not restore-verified. Use `/usr/bin/stat`.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
