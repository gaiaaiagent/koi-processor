# Vault Sync 1.5 Soak — Early Close Report

## Timeline

- **Start**: `2026-02-26T04:31:19Z`
- **Early close**: `2026-02-26T09:33:43Z` (~5 hours of 72h planned)
- **Reason**: Steward-directed acceleration (Darren Zal)

## Observed Health Trend

All 4 samples show zero drift and zero rejections across both peers.

| Timestamp | L.Pending | L.Scans | L.Rejected | L.Drift | P.Pending | P.Scans | P.Rejected | P.Drift |
|-----------|-----------|---------|------------|---------|-----------|---------|------------|---------|
| 2026-02-26T04:31:19Z | 2 | 50 | 0 | 0 | 0 | 38 | 0 | 0 |
| 2026-02-26T08:56:30Z | 2 | 297 | 0 | 0 | 0 | 299 | 0 | 0 |
| 2026-02-26T09:07:33Z | 2 | 308 | 0 | 0 | 0 | 310 | 0 | 0 |
| 2026-02-26T09:33:43Z | 2 | 333 | 0 | 0 | 0 | 336 | 0 | 0 |

**Key observations:**
- Watchers enabled on both peers throughout
- Scans progressing normally (333/336 scans in ~5h)
- 2 pending events on local node are stable (pre-existing, not growing)
- Zero rejections, zero drift across all samples

## Risk Acceptance

Proceeding with canary-first rollback-guarded rollout. This is a risk-accepted operational
decision, not a failed soak. The formal 72h soak gate is removed as a blocker.

## Monitoring

The soak cron continues running for ongoing monitoring. The durable log at
`vault-sync-soak.jsonl` will continue accumulating entries. Any regression will be
caught by the cron and visible in the trend data.

## Decision

**CONTINUE** — End formal soak early. Proceed to FR canary validation (Phase 2)
and staged rollout (Phase 3).
