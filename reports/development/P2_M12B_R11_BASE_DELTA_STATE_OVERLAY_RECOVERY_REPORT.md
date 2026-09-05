# P2-M12B-R11 Base+Delta Online State Overlay Recovery

## STATUS

`BLOCKED_ON_LIVE_HISTORY_OVERLAY_NORMALIZED_SCHEMA_INCOMPLETE`

## Evidence

R4's append-only official live-history collection is complete: 204 races and
2,130 runner rows through 2026-08-20; SQLite `quick_check` is `ok` and
`foreign_key_check` has zero rows. `P2HistoricalAsOfView` correctly proves
that an 2026-08-20 target sees through 2026-08-19 and an 2026-08-21 target
sees 2026-08-20.

However, that view only unions `race_key` and `race_date`. None of the actual
online builders consumes it. V1 directly queries a complete historical DB;
Class replays M03 materialized date/race/class structures; Speed consumes M04
curated observations; Pace consumes M05 curated observations. The live delta
does not contain the full normalized entities or derived frozen observation
inputs required by those existing paths.

## Why this stops here

Connecting the current delta would require silently omitting post-cutoff state
or recreating V1/Class/Speed/Pace state through four separate ad-hoc paths.
Either would violate the one-shared-overlay and exact-parity requirements. No
shadow-cutoff parity, live inference, bundle, prediction freeze, E2E, or
engineering replay was run.

The next recovery must first define and validate one read-only normalized
base+delta source that exposes the exact raw historical entities and frozen
M02/M04/M05 inputs consumed by all four builders. Only then may the required
July shadow-cutoff FS04 parity gate be attempted.
