# P2-WIDE-SCI-DIRECT-001 — WIDE ticket-level Market-offset direct residual

## Objective

Evaluate only the registered D0/D1/D2 direct WIDE ticket residual candidates
against the frozen calibrated M0 WIDE Market.  The model output is a scientific
race-normalized ticket mass; it is not promoted to an operational WIDE hit
probability or policy input.

## Inputs

- Frozen M0 and exact per-WF gamma values from
  `audit/data/p2_wide_sci_baseline_20260825/market_primary_manifest.json`.
- Development historical WIDE market/official-payout labels and canonical
  primary universe through `2026-07-31`, read-only.
- Frozen FS04 runner matrix/metadata and FS04 feature manifest.
- Frozen H2-C04 LightGBM core, H1-C06 fixed parameter source, and WF1–WF3
  walk-forward boundaries.

## Candidate contract

- D0: exact frozen V1 240-feature pair contract only.  The copied V1 runner
  CSV exists, but its required `frozen_wide_feature_list.csv` and
  `frozen_wide_category_vocabulary.csv` are absent.  D0 is therefore recorded
  `D0_UNAVAILABLE`; no V1-like reconstruction is permitted.
- D1: all 178 FS04 columns transformed per canonical unordered pair into mean
  and absolute-difference values; non-finite input produces NaN.  All 356
  columns remain in the model matrix; none are selected away.
- D2: D1 plus only `log(upper_odds/lower_odds)`.

## State and invariants

1. The current primary-market manifest is validated as M0 and supplies the
   three exact outer-fold gamma values.
2. For each fold, primary/complete historical pair rows before the outer
   validation start constitute inner/outer training; the fixed M0 gamma is
   applied to raw M0 q to form the offset.  The adapter presents `log(q_M)`
   with backend gamma `1.0`, which is algebraically exactly the frozen M0
   offset and performs no new gamma fit.
3. H2's existing inner early-stop / outer fixed-iteration LightGBM primitive
   consumes fractional labels 1/3 for each of the three official payout pairs.
   The outer validation labels never enter fitting or early stopping.
4. A pair-row structural index is used only to satisfy the generic runner
   ordering interface; model features contain no ordered horse-number slot.
5. Per race, q sums to one and residual zero returns M0 exactly.  D1/D2 pair
   transformations are tested over every pair for horse-swap invariance.

## Exclusions

- No August source or outcome, no PL/WIN probability feature, no direct-residual
  tuning/search, no clipping, no economic/ROI/threshold analysis.
- No DEV-LIVE, WIDE_OPS_V0, Policy, or production DB change.

## Outputs

`audit/data/p2_wide_sci_direct_20260825/` contains candidate metrics,
primary-selection manifest, pair predictions, feature/D0 availability contract,
bootstrap, residual and probability audits, fixed-budget record, implementation
report, checkpoints, and a run manifest.

## Acceptance

- Validation set is exactly the frozen 481 races / 29,136 pairs for every
  available candidate.
- D0 availability is explicitly resolved without reconstruction; D1/D2 use
  only their registered feature transformations and fixed H2 parameters.
- All probability, source, leakage, swap, duplicate, and deterministic-repeat
  audits pass.  A new primary is frozen only if the best available delta is
  strictly negative.
