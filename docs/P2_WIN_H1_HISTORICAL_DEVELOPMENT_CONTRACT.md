# P2 WIN H1 Historical Development Contract

`P2-M09-RESUME` is the first formal registered six-configuration H1 evaluation
after the permanently recorded pre-formal inner-validation incident
`P2-INC-001`. It is not the first observation of any historical performance.

Formal execution requires `P2_FORMAL_M09_EVALUATION=1`, the passing
`P2_M09_INCIDENT_RECOVERY_V1` state, formal H1 budget `0/6`, and the frozen
M08B backend, grid, folds, FS00 feature list, gamma method, and selection rule.
The incident's March-to-April two-tree, gamma-1.0 probe is excluded from every
formal early-stopping, configuration-selection, pooled-metric, bootstrap,
model-artifact, and formal-budget result.

Each of six configurations runs once in WF1–WF3. The only tree features are
the 119 FS00 legacy fields. q/log-q are Market offset metadata, not features.
Gamma is fitted once per fold and training scope, then shared by all six
configurations. Inner early stopping includes iteration 0 (calibrated Market),
uses the frozen candidate race-equal soft-target multinomial log loss,
`max_boost_round=1000`, patience 50, tie tolerance `1e-10`, and smaller-
iteration tie break. A zero-tree winner exactly returns calibrated Market.

Selection pools all outer May–July race deltas equally and selects the smallest
candidate-minus-calibrated-Market mean, with the frozen `1e-5` tie rule.
Bootstrap, monthly, venue, stability, and residual-score diagnostics occur only
after selection and never alter it. The evidence fields are
`HISTORICAL_MARKET_TIME_UNKNOWN`, `DEVELOPMENT_REFERENCE_ONLY`, and
`DEVELOPMENT_EVALUATION_WITH_RECORDED_PROTOCOL_INCIDENT`; no T-15 equivalence,
Primary gamma freeze, or confirmed probability edge follows from this work.

## Registered result

The one permitted formal run evaluated all six configurations across WF1–WF3
and exhausted the formal budget `6/6`, while retaining a separate incidental
peek count of `1` for `P2-INC-001`. `H1-C06` was selected by the frozen pooled
race-equal delta rule with candidate LL `1.7457589458187202`, calibrated-Market
LL `1.7449180350404083`, and delta `+0.0008409107783122695`. The frozen H1
decision is `H1_HISTORICAL_NO_SIGNAL`. No additional H1 configuration,
parameter change, feature action, recalibration, clipping, or rerun is
authorized; this result does not block independent H2 development.
