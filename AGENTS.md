<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-14 12:47 PDT

**Current status:** `fix/document-ingest-integrity` @ `3bbe405`, clean and pushed. **Draft PR #66** (0 checks — do NOT merge) now closes #62 and **#68**. #68 landed **in the branch only**: `api/document_extraction_contract.py` is the single source, all four prompt/schema surfaces derive from it, `Document`/`Event` admitted. Migrations 124/125 live; **126 written and NOT applied** (verified no-op). `substack-deep-extract` still **disabled and unloaded** (backlog 356).

**Next:** 1) **#67** — `retract_fact` emits no federation event; peer application unproven 0/4. 2) **#69** — transactional rollback/replay; stale entity links are why "retracted" ≠ "repaired". 3) Then the pinned replay (task `koi-2026-09-14-michaelgarfield-pinned-replay`, due 09-21) — curated payloads ready, no `--force`. Do NOT deploy #66 or re-enable the job before these.

**Watch:** 3 michaelgarfield docs still HALF-repaired (facts retracted; entity links + discourse moves stale). All three now pass the gate with the essays typed `Document`. Three live `Project` rows that are arguably `Document`s need an operator `/entities/retype`, not payload curation — see the handoff. Do not add the gate-catalog floors yet.

**Verification:** tree clean, `git diff --check` clean; 107 tests across the affected files, 214 across every suite touching the extractor/identity. Full suite vs a clean worktree at the merge-base: **67 failures/errors, identical sets — zero regressions** (the earlier "3 pre-existing" undercounted). No canon validator here. Read-only gate: `scripts/check_document_integrity.py` (now also `--type-decisions`).

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
