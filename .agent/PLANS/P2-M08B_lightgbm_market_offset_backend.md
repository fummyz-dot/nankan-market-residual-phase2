# P2-M08B — LightGBM Market-Offset Race-Softmax Backend Foundation

## Inputs

- M08A WIN q / calibration contracts and historical reference Market dataset.
- M06 FS00 legacy matrix and lineage registry.
- M07 target-universe and WIN soft-target outcome datasets.

## Outputs

- Frozen LightGBM backend, custom race-softmax probability/objective layer, FS00 training frame, walk-forward and six-config registry.
- Engineering-only fixture model, determinism/save-load audits, manifests, contracts, tests, and report.

## Invariants

- LightGBM is the only backend; no H1 performance evaluation in this job.
- q is an offset only, never a model feature; gamma is fitted only within training folds.
- Race-softmax grouping is contiguous and deterministic; zero residual returns calibrated Market exactly.
- FS00 has exactly 119 features; prospective stabilization/outcomes, payout, ROI, and Keibabook are excluded.
- Historical MARKET_TIME_UNKNOWN remains development-reference-only, and T-15/Primary gamma remain unfrozen.

## Acceptance checks

- Analytic gradient / diagonal-Hessian finite-difference checks and invariance checks pass.
- FS00 join is exactly one-to-one for the eligible historical Market reference universe.
- Nested walk-forward protocol and exactly six configurations are registered without executing them.
- Engineering fixture save/load and repeated-fit predictions are deterministic.
