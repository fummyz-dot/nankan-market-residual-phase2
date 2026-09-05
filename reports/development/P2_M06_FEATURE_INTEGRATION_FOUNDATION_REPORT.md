# P2-M06 — Feature Integration Foundation Report

## STATUS
`READY_FOR_P2_M07_TARGET_UNIVERSE_AND_MODEL_FOUNDATION`

## V1 inventory, active port, and parity
The active Phase 2 port contains exactly 119 F0/F1/F2/F3/F5/F6/F7/F8 features. Immutable V1 overlap parity passed for 245,208 rows with zero null-mask/value mismatches and maximum numeric difference 0. The full historical-development roster retains 250,093 rows; its 4,885 rows absent from the V1 starter-only artifact are retained without labels. A frozen Phase 2-owned static horse-semantic map preserves V1 categorical semantics without runtime dependency on `reference/v1/`.

## Integrated blocks and safety
Class, Speed, and Pace each joined one-to-one. The matrix has 178 model columns: V1 119, P2_CLASS_RULE 8, P2_CLASS_EMPIRICAL 7, P2_CLASS_UNCERTAINTY 9, P2_SPD 15, and P2_PACE 20. It omits outcomes, current body weight, Market, Keibabook, P2_BIAS, P2_CURRENT, and P2_EXT. Same-day/future/current-outcome use and post-cutoff rows are zero. Eligibility is metadata only.

## Roster limitation and next stage
The matrix is `HISTORICAL_DEVELOPMENT_ROSTER`, not a claim of T-15 active-roster equivalence. Field-composition blocks must be recomputed from a prospective active roster. FS00–FS04 are frozen before performance work. Year-partitioned independent rebuilds matched for every year from 2020 to 2026; logical matrix hash: `efa7a8211100ec8dfa0550edefcdb8404e9562271bada135be674cd332123129`.
