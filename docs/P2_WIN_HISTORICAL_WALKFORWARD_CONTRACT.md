# P2 WIN Historical Walk-Forward Contract

Historical 2026-03 through 2026-07 Market remains `HISTORICAL_MARKET_TIME_UNKNOWN` and `DEVELOPMENT_REFERENCE_ONLY`; this protocol is not actual T-15 evidence and cannot freeze the Primary gamma parameter.

## P2-INC-001 recovery wording

`P2-INC-001` is a recorded pre-formal inner-validation two-tree probe on
March-to-April data.  It is preserved as an incident and excluded from formal
selection.  Subject to the M09R integrity audit, the next execution is the
**first formal registered six-configuration evaluation after recorded
pre-formal inner-validation incident P2-INC-001**.  It must use the recovery
guard `P2_FORMAL_M09_EVALUATION=1`; an unregistered real-data invocation hard
fails.  This wording does not claim that no performance had ever been observed.

All four South Kanto venues are pooled. The fixed outer folds are:

- `WF1`: train 2026-03-01–2026-04-30; validate 2026-05-01–2026-05-31. Inner train March; inner validation April.
- `WF2`: train 2026-03-01–2026-05-31; validate June. Inner train March–April; inner validation May.
- `WF3`: train 2026-03-01–2026-06-30; validate July. Inner train March–May; inner validation June.

For every configuration, M09 must: fit gamma and categorical preprocessing on the inner-train dates only; train a residual model; early-stop by candidate race-equal soft-target multinomial log loss on inner validation (`max_boost_round=1000`, patience `50`); record best iteration; refit gamma/preprocessing on the full outer train; retrain exactly that configuration for the recorded iteration; and evaluate the outer validation. Venue folds are prohibited.

## ZERO_TREE_BASELINE_EARLY_STOPPING_CLARIFICATION_V1

Iteration 0 is the already-frozen nested Market-only state (`f=0`). Its inner-validation candidate probability and loss must equal calibrated Market within `1e-12`, and it participates in the same argmin as trained iterations 1–1000. The inner selection tolerance is `1e-10`; ties select the smaller iteration, therefore retaining iteration 0 when a tree model is materially indistinguishable from Market. Patience is 50 rounds without improvement over this iteration-0-inclusive best. If iteration 0 wins, the outer retrain creates no residual tree model, emits an explicit zero-tree baseline record, and its outer candidate must equal calibrated Market within `1e-12`. This is an implementation consequence of M08B's frozen nesting, not a configuration or search-axis addition.

The registered H1 grid has exactly six LightGBM configurations, differing only in `(max_depth,num_leaves)` of `(2,4)`, `(3,8)`, `(4,16)` and `lambda_l2` of `10` or `50`. All share the frozen common parameters in `P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml`. No seventh configuration or backend is authorized.

M09 selection uses the pooled, race-equal mean of every outer-validation `candidate LL - calibrated Market LL`; it does not average three monthly means equally. Lower is selected. Within `1e-5`, tie-breaking is larger L2, then shallower depth, then lexical ID. Venue, best-month, bootstrap significance, or ROI never select a configuration. The resulting historical status is only `H1_HISTORICAL_DEVELOPMENT_SIGNAL` if pooled mean delta is negative, otherwise `H1_HISTORICAL_NO_SIGNAL`; either result does not establish a Phase 2 probability edge, and H1 no-signal does not block H2.

The registered M09 resume used this protocol exactly once and selected `H1-C06`.
Its positive pooled delta yields `H1_HISTORICAL_NO_SIGNAL`; the six-configuration
H1 budget is exhausted and no rerun or rescue search is authorized.
