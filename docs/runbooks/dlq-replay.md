# DLQ Replay Runbook

## When to run

- Drift report flags peer X with > 100 DLQ entries
- Manual reconciliation after Darren confirms his side recovered
- Phase-11a validation gate ("DLQ count drops to 0")

## Pre-flight

1. Confirm peer reachable: `curl http://<peer-url>:8351/koi-net/health`
2. Confirm peer not in backoff: `psql -d personal_koi -c "SELECT backoff_streak, backoff_until FROM koi_net_nodes WHERE node_rid = '<rid>'"`
3. DLQ count: `psql -d personal_koi -c "SELECT dlq_reason, COUNT(*) FROM koi_net_events_dead WHERE target_node = '<rid>' GROUP BY dlq_reason"`

## Replay

### One peer
`./venv/bin/python -m api.dlq_replay --peer <node_rid> --limit 500`

### One specific event
`./venv/bin/python -m api.dlq_replay --id <dlq_id>`

### Everything (use sparingly)
`./venv/bin/python -m api.dlq_replay --all --limit 1000`

## Verify

- Live queue: `psql -d personal_koi -c "SELECT COUNT(*) FROM koi_net_events WHERE delivery_attempts = 0 AND target_node = '<rid>'"`
- Logs: `journalctl --user -u personal-koi.service -f | grep <rid>`
- Brief: morning brief shows DLQ count dropping.

## Rollback

DLQ rows are deleted on replay. Replayed events are content-addressed (same `rid`) — duplicates are idempotent on the remote side. If you replay to the wrong peer, replay again with corrected `--peer` is safe.

## Common errors

- `DLQ row N not found` — already replayed.
- `UniqueViolationError` — event already in live queue, safe to ignore.
- Replay succeeds but peer never picks up — check `koi_net_nodes.backoff_until`.
