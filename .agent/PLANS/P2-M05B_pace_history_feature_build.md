# P2-M05B — Strict-As-Of NAR Main Pace History Feature Build

## Scope

Build `P2_PACE_MAIN_V1` only from M05A-approved NAR runner closing and exact
race pace-balance observations. Produce separate formal observations and a
calendar-date strict-as-of target runner feature table.

## Invariants

- Main history accepts finite closing relative/rank observations and finite
  standardized race pace balance only, strictly before target date.
- Exchange and other-flat observations never enter state; exchange targets may
  receive pre-race features.
- Pace balance uses fixed robust course hierarchy/median/MAD/floor only;
  no class, speed, Market, Keibabook, corner, runner first-3F, decay, or
  distance-similarity input is permitted.
- Current-race and same-day results are locked out. Cold values are NULL, not
  zero-imputed.

## Acceptance

- 250,093 target rows; source observation parity to M05A; deterministic two-run
  logical hash; source-boundary and leakage audits equal zero.

## Completion record

- Status: `READY_FOR_P2_M06_FEATURE_INTEGRATION_FOUNDATION`.
- Formal runner/race observations and 250,093 date-block target features were
  built under `data/curated/p2_pace/`.
- Logical rebuild hash: `eae8fd93990f2746a69ff5daecd185d44de2fd4eb61a67d32457f84aa93079db`.
- Same-day/current-race/exchange/other-flat, runner-corner, runner-first-3F,
  Keibabook, Speed/Class, and Market-use audits passed with zero prohibited use.
