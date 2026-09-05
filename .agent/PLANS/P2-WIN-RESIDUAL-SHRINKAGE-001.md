# P2-WIN-RESIDUAL-SHRINKAGE-001 — H2-C04 OOF residual shrinkage

## Scope

Evaluate only the preregistered one-parameter family
`p_lambda = softmax(log(q_market) + lambda * log(p_current/q_market))`
on saved H2-C04 outer OOF rows.  This is a development-only audit; it does not
train a model or alter live inference, policy, features, or WIDE research.

## Inputs

- `data/curated/p2_model/win/h2/h2_nar_core_outer_runner_predictions_v1.csv.gz`
  filtered to `H2-C04` / `FS04_LEGACY_SPD_PACE_CLASS_FULL`.
- `audit/data/p2_m08b/walkforward_fold_manifest.csv` for the frozen WF1–WF3
  temporal safety proof.
- The embedded `win_soft_target` winner labels in the saved OOF artifact.

## Outputs

`audit/data/p2_win_residual_shrinkage_20260826/` will contain the requested
OOF inventory, fold lambda report, runner predictions, paired LL/bootstrap,
calibration/residual diagnostics, development-only `lambda_devfull`, search
budget, implementation report, and a provenance run manifest.

## Invariants and exclusions

- Development dates are bounded by `2026-07-31`; neither August nor result DB
  is read.
- Every included race has one winner, exact runner identity, positive finite
  candidate/Market probability, and each distribution sums to one.
- WF2 lambda is fitted from WF1 only; WF3 lambda is fitted from WF1+WF2 only;
  WF1 is excluded from the primary shrunk comparison.
- Lambda endpoints reproduce Market/current to `1e-12`; optimizer only chooses
  among endpoint 0, endpoint 1, and one bounded scalar optimum.
- No model fitting, feature change, configuration/policy/live-code change, or
  economic evaluation is allowed.

## Failure handling

- Any OOF/fold/probability/roster/label invariant failure aborts before output
  promotion.
- Missing or non-provable OOF rows are excluded only with an explicit reason in
  the inventory.  A temporal fit with no prior OOF race is never improvised.
- Output files are written atomically; the run manifest records `vcs_mode:none`.

## Tests

- Endpoint identities, normalization, convexity and analytic gradient.
- Endpoint and interior optimizer cases.
- WF2/WF3 temporal leakage regression, identical comparison sample, and
  deterministic calendar-date bootstrap.
- Development-date/August boundary rejection.

## Acceptance

- Fresh Python process completes the audit and requested artifacts.
- All hard invariants and deterministic rerun checks pass, with source/result
  DB access and production DB mutation recorded as zero.
