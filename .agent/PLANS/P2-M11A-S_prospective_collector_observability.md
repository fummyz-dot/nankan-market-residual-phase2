# P2-M11A-S — Prospective Collector Observability & Fail-Fast Safety

## Inputs

- Existing foreground `prospective_day_collector`, current-info SQLite schema, and 2026-08-19 fixture.

## Outputs

- Read-only `--preflight` and `prospective_collection_status` commands.
- Atomic per-race, daily live, heartbeat, and event artifacts.
- Race-scoped versus day-fatal status separation.

## Invariants

- No outcome, performance, ROI, or model access.
- Preflight starts no capture wait; status command writes nothing.
- Captures remain foreground and no-backfill.

## Acceptance

- Existing fixture presents T15 as `LATE_AFTER_DECISION`.
- 2026-08-20 preflight discovers/schedules without a capture.
- Tests cover waiting/late/missed/failed states, atomic writes, heartbeat, resume reconstruction, and failure scope.

## Status

In progress.
