# P2S JOB008 — Stage2 Confirmatory Live Runtime

## Inputs

- Accepted JOB007R3 implementation and local development replay artifacts.
- Read-only `db/market_snapshot.sqlite` T15 current/WIDE rows.
- Frozen Stage2, scorer, target-source, and confirmatory-live authorities.
- The existing `./specialized-collect` supervisor and collector lifecycle.

## Outputs

- One isolated Stage2 worker process launched automatically by
  `./specialized-collect`.
- Immutable confirmatory or late-diagnostic prediction artifacts plus an
  append-only worker ledger under
  `outputs/successor_v1/stage2_confirmatory_live/`.
- A development bootstrap manifest bound to accepted JOB007R3 artifacts.
- Synthetic prelive audit and blinded Git-tracked evidence.

## Invariants and exclusions

- Collector capture and exit status never depend on Stage2 worker success.
- The worker opens the market database with SQLite `mode=ro` and `query_only`.
- The worker performs no network access and never imports a network collector.
- Confirmatory classification requires race date `>=2026-09-07`,
  `T15_STANDARD_ELIGIBLE`, and immutable freeze completion no later than the
  stored T15 decision time.
- A late start, late inference completion, or crash-before-freeze can never be
  promoted on restart; it is recorded only as `LIVE_PREDICTION_LATE`.
- Development replay rows may seed state but are always
  `formal_support_eligible=false`.
- Same-date reconciliation/state updates cannot enter scoring.
- No real confirmatory row is created by JOB008; only synthetic/fake-clock
  execution is allowed before Research Lead acceptance.
- No performance aggregate, formal Stage2 evaluation, betting, market DB
  write, model retraining, or frozen-science change.

## State transitions and idempotency

1. `DISCOVERED` identifies a complete eligible local T15 row.
2. `SCORING` is appended before inference.
3. If completion time is within the decision deadline, atomically create one
   immutable prediction and append `PREDICTION_FROZEN`.
4. Otherwise append `LIVE_PREDICTION_LATE`; restart preserves that terminal
   exclusion.
5. Existing identical immutable artifacts are reused; conflicts hard-fail the
   worker only.
6. Worker heartbeat/status files are atomic. Collector shutdown is independent
   and records worker state without changing collector success/failure.

No database transaction is added: the market DB is read-only and worker state
is append-only files with atomic immutable promotion. Timestamps are aware UTC;
race-date eligibility is the stored canonical local race date.

## Validation

- Authority/package hashes and accepted R3 artifact/model hashes.
- Fake-clock timing boundary, late reconstruction, crash/restart, duplicate,
  same-day isolation, network denial, read-only DB, collector independence,
  and development-support exclusion tests.
- Prelive run creates exactly zero real confirmatory rows.
- Blinded evidence validation, worktree cleanliness, two commits, normal push.
