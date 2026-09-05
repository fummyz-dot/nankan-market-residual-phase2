# P2-WIN-MARKET-LEAD-LAG-V0-001 — COMPLETE

## Scope

Implement an outcome-free, research-only diagnostic that compares the frozen
T15 DEV-LIVE-V1 C0 distribution with already committed WIN market-trajectory
events at T15, T10, and T05.  It never supplies a Main feature, ticket, stake,
policy input, collector action, or result access.

## Inputs

- Immutable Main Recommendation Evidence: exact T15 C0 and P2-primary status.
- `win_market_trajectory_mark_events`: append-only collector-explicit WIN
  events only.  T10/T05 are diagnostic future information.
- Frozen DEV-LIVE-V1 model hash and the existing calibrated market gamma.

## Outputs

- One immutable Lead/Lag research evidence record per race/frozen bundle after
  T05 completion or source-only post-time finalization.
- JSON audit envelope and an aggregate research-only summary.
- A frozen V0 science/bundle manifest.

## Invariants and exclusions

- `M_t` is the existing gamma-calibrated inverse-odds market distribution;
  no new fit, source fetch, or odds repair.
- Primary requires exact T15/T10/T05, complete equal active rosters, T15
  standard Main reference, P2-primary status, and post-freeze provenance.
- PRE_RACE_FALLBACK, RECOVERY, partial marks, roster changes, invalid values,
  and 2026-08-28 are excluded from Primary.  No source is overwritten.
- Result, winner, payout, and result HTTP/DB access remain zero.

## Acceptance tests

- Complete/positive/negative/no-movement KL and cosine cases.
- Missing/incomplete/recovery/fallback/roster-change exclusions.
- Idempotent replay and immutable-payload conflict.
- 8/28 engineering exclusion, no result access, Main invariance, and
  fresh-process T15/T10/T05/restart smoke.

## Completion

- Frozen bundle: `85bb78c19ef06599156500d13ca368cd5eeb8e527f63e94e33b2642f122d762c`
- Confirmation start: `2026-08-28T15:08:51.238307+00:00`
- Unit/integration/regression suite: 105 tests passed.
