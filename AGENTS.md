<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-05 01:15 PDT

**Current status:** `regen-prod` @ `10a19ad`, published, 0 ahead / 0 behind, tree clean, 115 passed / 2 skipped. Nothing half-applied here; one fix is committed but NOT live.

**Next:** 1) Make the axios fix live — `personal-koi-mcp` @ `409fe9d` is unpublished and `node_modules` still holds 1.12.2; publishing it also publishes another session's commit (a decision), then `npm install` + restart via `scripts/enumerate-mcp-processes.sh` (never carry a process count). 2) Re-scope the NUC parity monitor BEFORE building it — task `koi-nuc-parity-monitor-rescope`, fresh eyes. 3) Vocabulary decision 9315/9317 — operator, cold facilitator.

**Watch:** The NUC topology paragraph was rewritten SIX times on 2026-09-04 — re-run the reproduce commands in `docs/operations/two-node-topology.md`, never edit from memory. `deploy.sh`'s koi-processor leg is BLOCKED (158 deletions vs limit 5). `stat` is shadowed and KILLED (exit 137) on BSD flags; `/usr/bin/ps` does not exist; `gh` eats stdin in loops.

**Verification:** 115 passed / 2 skipped. Every exemption register carries a staleness assertion; all controls run and restored. No canon validator here.

Full source of truth: `PROJECT_HANDOFF.md`.
<!-- end-skill:handoff:end -->
