# P2-M03A — Empirical Class Rating Protocol Report

## 1. STATUS
`EMPIRICAL_RATING_VALIDATED`

## 2. Rating universe
South Kanto only. 21343 races created safe pairwise updates; 329 exchange/bare-exchange races were excluded. Other-flat and Ban'ei result updates: 0.

## 3. Result-status semantics
Only `FINISHED` with a positive numeric finish position is pairwise-safe. `RAW_FINISH_STATUS_MISSING` (5588 South Kanto runner rows) was excluded without inferred rank.

## 4. Bradley–Terry formulation
The engine uses `sigmoid(R_i - R_j)`, race-size-normalized mean pair residuals, and simultaneous updates from frozen pre-race scores.

## 5. Same-day as-of
Every date was processed as a calendar-date block. The audit found 0 pre-state updates on or after the current date.

## 6. K configurations and selection
R1=0.25, R2=0.50, R3=1.00 were the only candidates. Selection used 2021–2024 race-equal pairwise log loss only; selected `R3` (`K=1.00`).

## 7. Validation and diagnostic
2025 validation LL: 0.613603516990; neutral `log(2)`: 0.693147180560; delta: -0.079543663570. 2026 Jan–Jul diagnostic LL: 0.609544191123; it did not alter selection.

## 8. Cold starts and transfers
Initial ratings are exactly 0.0. 20056 pre-race rows were cold starts; transfer-group cold starts: 9936. Other-flat history never seeds Main ratings.

## 9. Context-prior feasibility
Context candidates were audited using pre-race ratings only with a date-blocked exact/top-or-taxonomy/ruleset/global fallback hierarchy. No performance optimization of the hierarchy was performed.

## 10. Other-flat isolation
`OTHER_FLAT_NAR` and Ban'ei updates are zero. `P2_XVENUE` model use remains unapproved.

## 11. Data quality
No unknown result-status vocabulary was observed. Historical program points and boundary positions remain unavailable and were not created.

## 12. Next stage
M03B may build the frozen empirical fields.
