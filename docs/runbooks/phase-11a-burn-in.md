# Phase 11a Burn-In Protocol

## Goal

Demonstrate 7 consecutive days of green KOI federation operation before declaring Spore Phase 11a Shawn-side validation complete.

## Daily monitoring (automated via claude-rhythms morning brief)

The `koi-federation-investigator` agent surfaces in every morning brief:

- ✓ Canary green within last 24h
- ✓ DLQ 24h count = 0 (or trending toward 0)
- ✓ No peer with `backoff_streak > 0` for > 1h
- ✓ Drift sweep < 5 RIDs/type delta (once Dobby `/rids/fetch` returns populated)
- ✓ All 3 health endpoints respond 200

## Sign-off (day 7)

All four must be true:

1. 7/7 daily canary green
2. DLQ 0 events moved last 24h
3. Drift sweep zero or < 5 RIDs/type
4. `koi_burnin_report.py` renders all ✓

## On day 7

```
~/miniforge3/bin/python3 ~/.claude/local/scripts/koi_burnin_report.py
~/miniforge3/bin/python3 ~/.claude/local/scripts/koi_phase11a_handoff.py
```

This generates the validation report + drops the vault `.md` for Darren + prints
outbox + Telegram reminders.

Then manually:
- Outbox draft via `mcp__plugin_claude-outbox_claude-outbox__outbox_draft_create`
- Telegram Darren

## On failure (any day fails)

1. Reset 7-day window
2. Open backlog task with root cause
3. Fix
4. Restart burn-in

## Known issues at burn-in start (2026-05-18)

- Dobby `/koi-net/rids/fetch` returns empty array. Drift sweep cannot establish baseline. Resolve before declaring sign-off.
- WireGuard tunnel to Darren's MacBook dead since 2026-03-17 (200+ ConnectTimeouts). Dobby relay still healthy. Sign-off should require either Darren MacBook tunnel restored OR explicit acknowledgement that Dobby-only relay is acceptable.
