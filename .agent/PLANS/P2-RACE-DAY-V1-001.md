# P2-RACE-DAY-V1-001 — One-command race-day orchestration

## Objective

Provide `./race-day` as the normal manual-betting operational entrypoint.  It
reuses the live-history update, official collector, pre-race resolver,
`race-shadow`, recommendation evidence, official-result collector, and
settlement evaluator without changing their model, policy, or source
semantics.

## Inputs and outputs

- Inputs: official day/card discovery, prior-day meeting-aware history,
  immutable DEV-LIVE-V1/FS04/policy artifacts, existing pre-race capture DB,
  and existing evidence/result/evaluation stores.
- Durable outputs: atomic immutable day manifest and append-only day events in
  `outputs/live_development/YYYY-MM-DD/<venue>/`.
- Normal CLI: `./race-day [--date YYYY-MM-DD] [--venue VENUE]`.

## Invariants / exclusions

- The target-date history boundary is always `D - 1`; target-date results,
  payout sources, result collector, and evaluator are not data-accessed until
  every planned Primary target has a terminal pre-race state and the last
  target's scheduled post time has passed.
- The plan is immutable after the first successful static preflight.  A
  material card conflict fails closed; withdrawal/active-roster changes remain
  runtime pre-race semantics rather than plan rewriting.
- `race-shadow` remains the single pre-race resolver and recommendation/evidence
  writer.  Existing evidence is re-displayed, never re-decided.
- The orchestrator neither trains/scores a different model nor reads/writes
  `actual_bets`, purchases, orders, fills, or result data during PRE_RACE.
- Existing collector/result/evaluator idempotency remains authoritative.

## States and boundaries

1. `DAY_STARTED` → history/hash/integrity/static preflight → immutable plan.
2. Each Primary target is `WAITING`, `ANALYSIS_READY`, `SKIPPED_TOO_LATE`, or
   race-scoped `BLOCKED`; non-Primary races are `NOT_TARGET`.
3. All pre-race terminal targets + last target post time reached:
   `PRE_RACE_OPEN → PRE_RACE_CLOSED → POST_RACE_OPEN`.
4. Only in POST_RACE, collect official results, wait for existing
   `RESULT_OFFICIAL_FINAL` / payout readiness, evaluate, and emit
   `DAY_COMPLETE` or `DAY_WAITING_RESULTS_TIMEOUT`.

## Failure / resume / locking

- An advisory `flock` owns one date/venue process lifetime and naturally
  releases after crash/reboot.
- Manifest/checksum verification makes restart deterministic.  Existing
  recommendation evidence is source-of-truth on restart.
- Result timeout is safe and resumable.  Ctrl-C stops the managed collector,
  flushes the durable event record, and prints safe-to-resume status.

## Targeted acceptance

- Morning/restart/fallback/existing-evidence/late-start/withdrawal/partial-
  WIDE/non-target-last-race/result-incomplete/timeout/lock/Ctrl-C scenarios.
- Fresh-process temporary-fixture smoke proves no pre-race result access,
  automatic evidence/no-freeze behavior, and post-race collector/evaluator
  ordering.
