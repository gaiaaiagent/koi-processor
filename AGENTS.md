<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-26 11:05 PDT

**Current status:** Tree clean at `61b8fb1`, synced with origin. Full suite **43 failed / 1586 passed** with **no `--ignore` needed** — zero new vs the measured `763ede4` baseline. The iCloud conflict sweep (`com.personal-koi.vault-conflict-sweep`, 30-min) is **canary-proven firing unattended**; conflict files at 0.

**Next:** 1) **koi 8386** — re-measure entity creation after the strict flip; clock-gated to **08-31**, nothing to do before then. 2) *(optional)* 2 pre-existing gap notes + 6 unregistered notes; an unrun defect-class sweep on the `curl -sf … || echo '<fallback>'` idiom.

**Watch:** `test_vault_sync.py` has **order-dependent** failures — they pass in isolation and did not reproduce at clean HEAD, so a count delta there is not evidence of a regression. The iCloud hazard is **detected, not eliminated**: the vault is in iCloud sync with several concurrent writers. `docs/planning/` + `docs/soak-results/` are gitignored — docs written there are silently never committed. **This checkout is shared**: scope every `git add` to your own files.

**Verification:** measured at `61b8fb1`. Gate 10/10; governance 4/4; 73 focused tests. An earlier 46-failed reading was an artifact of a concurrent session's uncommitted work, not a regression.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
