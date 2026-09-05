# P2-M04B — Strict-As-Of Runner Speed History Feature Build

## Scope

Convert the frozen M04R course-only runner-speed observations into the approved
`P2_SPD` pre-race history block. The output remains
`PROVISIONAL_DEVELOPMENT_FEATURE`; this job neither tests speed performance nor
changes the amended standard.

## Inputs

- Read-only M04R course-only race and runner rebuild artifacts
- Read-only Phase 2 historical context DB for target race identity only
- `P2_SPEED_STANDARD_MAIN_V1.yaml` and P2-AMEND-001

## Invariants and exclusions

- One observation dataset (post-race historical fact) and one feature dataset
  (pre-race target input) remain separate.
- Date D target rows see only non-exchange, finite-z Nankan observations before
  D. Current-race and same-day observations are excluded.
- Other-flat, class/rating, going-conditioned, time-decayed, similar-distance,
  Market, odds, payout, and result-label inputs are excluded.
- Only the explicitly registered last/recent/exact-course fields and count
  metadata are emitted. Cold starts use NULL aggregates, never zero imputation.

## Acceptance

- 250,093 Nankan target runner feature rows and M04R parity for speed figures.
- Deterministic logical hashes, current-race/same-day/exchange/other-flat audits
  equal zero, and the feature registry and contract are frozen.
- Foreground-only execution; no child or background worker.

## Completion record

- Completed foreground on 2026-08-19.
- M04R course-only `speed_seconds` observation parity is exact for 244,367 rows;
  finite-z, non-exchange Main history eligibility is 242,883 observations.
- Two strict-as-of feature builds have identical logical hash. Status:
  `READY_FOR_P2_M05_PACE_FOUNDATION`.
