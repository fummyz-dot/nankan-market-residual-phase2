# P2-WIN-HORSE-STATE-V0-001 plan

## Scope

Validate exactly one development-only WIN challenger,
`WIN_HS_V0_TD_SPEED_HL60`: frozen FS04 (178) plus one numeric feature
`HS01_TD_SPEED_HL60`.  This is not a production/live feature integration.

## Inputs and frozen contracts

- H2-C04 frame, outer OOF predictions, WF1/WF2/WF3 manifest, and per-fold
  calibrated-Market gamma remain authority.
- P2_SPEED_STANDARD_MAIN_V1 `speed_z` observations are the only HS01 source.
- HS01 uses all finite strictly-prior (`race_date < target_date`) observations
  with `exp(-ln(2) * age_days / 60)`, no cutoff and no imputation.
- H2-C04 LightGBM market-offset objective, parameters, seed, categorical
  preprocessing, zero-tree early-stop, and iteration selection are reused.

## Outputs

`audit/data/p2_win_horse_state_v0_20260826/` receives the requested feature,
coverage, fold/model, OOF, metric, diagnostic, search-budget, implementation,
and run-manifest artifacts.

## Invariants and exclusions

- 179 tree features exactly; HS01 only, numeric, and missing remains NaN.
- Fold validation data and August outcomes never enter fitting.
- Market gamma/baseline remains frozen; no residual shrinkage fit.
- No results/official result database, payout/ROI, policy, DEV-LIVE, WIDE, or
  production database writes.
- Failure cases include same-day/future speed rows, roster mismatch, feature
  count mismatch, and invalid probability vectors.

## Test sequence

1. Pure HS01 formula and strict-date unit tests.
2. Feature materialization / FS04 identity / three live-provider parity
   engineering fixtures.
3. Fresh-process OOF run, deterministic rerun, output hash and hard-audit
   validation.
