# P2-M03A — Empirical Class Strength Rating Protocol & Configuration Freeze

## Scope

Implement and freeze the single `P2_CLASS_EMPIRICAL_MAIN_V1` online pairwise
Bradley–Terry rating configuration. The input is read-only South Kanto results
from `db/p2_history_context.sqlite` plus the P2-M02 race-class dataset. This
job does not create model features, train a model, read Market data, evaluate
market residuals, or evaluate ROI.

## Inputs

- `db/p2_history_context.sqlite` (read-only)
- `data/curated/p2_class_rule/nankan_race_class_rule.csv.gz`
- P2-M02 ruleset and mapping configurations

## Outputs

- Result-status registry, three-value K grid, and selected frozen configuration
- Prototype pre-race rating table and M03A audit artifacts
- Empirical-rating contract, report, and state/decision updates

## Invariants

- Updates use `NANKAN_TARGET` results only; other-flat and Ban'ei updates are zero.
- Explicit and bare/unresolved exchange races are excluded from updates.
- Ratings begin at zero; no transfer or name-based prior is used.
- All races on a calendar date observe only the state through the preceding date;
  updates are computed from frozen pre-race ratings and applied simultaneously.
- Candidate K values are exactly `0.25`, `0.50`, and `1.00`; selection uses only
  2021–2024, with the smaller K selected inside a `1e-4` tie tolerance.
- 2025 and 2026 are validation/diagnostic-only and cannot alter the selection.
- No Market, payout, popularity, odds, model, or ROI source is opened.

## Acceptance tests

- Pair direction, tie/status exclusion, simultaneous update, and race-size
  normalization tests pass.
- Same-date leakage is zero and next-date updates are visible.
- Other-flat, Ban'ei, and exchange results never update Main ratings.
- Grid/search-period constraints and Market source prohibition pass.
- Prototype output excludes result labels, output provenance is complete, and
  all selected-configuration artifacts are present.

## Completion

Completed foreground on 2026-08-19. `R3` (`K=1.00`) was selected only from
the registered 2021–2024 metric and its one-time 2025 validation was below
the neutral `log(2)` baseline. The selected configuration is frozen; no
additional rating candidate or Market-based evaluation was run.
