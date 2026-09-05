# P2-M11A-S-HOTFIX01 — Live Collector FK / Failure-State Hotfix

## STATUS

`READY_TO_RESUME_2026_08_20_LIVE_COLLECTION`

## P2-OPS-001

The 2026-08-20 Kawasaki 1R T20 `IntegrityError: FOREIGN KEY constraint failed`
is retained as a race-scoped operational incident. It is not a model or research
protocol incident. No outcome, performance, payout, or ROI source was accessed.

## Root cause and correction

`archive_bytes` created the ID used in raw filenames, but the collector did not
pass that ID to `record_capture`. The ledger therefore created a different ID;
the dependent snapshot references correctly failed. The collector now passes the
archive IDs into `source_captures`, then creates market/current child rows in one
explicit transaction with foreign keys enabled.

## Failure-state safety

Only a successful capture writes `.complete.json`, updates `last_completed`, or
emits `CAPTURE_COMPLETE`. Failures write `.failed.json`, leave the success state
unset, and emit `CAPTURE_FAILED` plus a race-scoped warning. The existing 1R T20
legacy failed `.complete.json` and its original event/checkpoint remain preserved
as `P2-OPS-001` evidence and cannot be promoted by resume logic.

## Verification

The deterministic regression suite covers the original missing-parent condition,
FK enforcement, success/failure checkpoints/events, resume behavior, and DB
`quick_check`/`foreign_key_check`. Re-preflight detected 12 official races and
the database checks are clean.
