<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-04 16:10 PDT

**Current status:** `regen-prod` @ `dcda7f7`, published, 0 ahead / 0 behind, tree clean, 120 passed / 4 skipped. Backup armed and proven unattended; `/entities/retype` now reversible (142 prior merges are not); flood fix live.

**Next:** 1) MCP supply chain — `personal-koi-mcp` has Dependabot DISABLED and `axios 1.12.2` executing in 11 processes; lockfile-only (task 9387). 2) Two-node problem — `deploy.sh` rsyncs the shared DEV checkout, NUC migrations are manual, drift-sweep is row-count-only. 3) Vocabulary decision (9315/9317, gates migration 113) — operator-only, and not to be facilitated by a session that authored the evidence.

**Watch:** Route presence does NOT prove module vintage — compare `ps -o lstart` to the **mtime** of the newest loaded source, not commit time. Before deploying, verify reachability: `git merge-base --is-ancestor <sha> origin/<branch>`. `predicate_raw` is satisfiable by construction, not evidence. Never `git clean -fd` in `koi-processor-runtime`.

**Verification:** 120 passed / 4 skipped; every fix proven by a positive control actually run. No canon validator here.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
