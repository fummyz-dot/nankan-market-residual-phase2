# P2-WIDE-J0-PROJECTION-AUDIT-001 — Calibrated Market joint feasibility audit

## Objective

Audit whether each frozen M0 calibrated normalized WIDE pair-mass vector in
the 481-race OOF intersection belongs to the convex hull induced by an
unordered Top3-set distribution. For infeasible vectors only, compute the
registered minimum `D_KL(q_m || q)` SLSQP projection. This is a reconstruction
audit, not a predictive model, calibration, or operational change.

## Inputs

- `audit/data/p2_wide_sci_baseline_20260825/fold_predictions.parquet`:
  pre-outcome construction reads only canonical race/pair keys, race metadata,
  and `q_M0_calibrated_oof`.
- Frozen M0 manifest and direct-D1 results only for authority and scale
  comparison.
- The `is_winning_pair` column is read only after all outcome-free LP and
  projection records are complete, solely for the registered CE-cost audit.

## Procedure and invariants

1. Build canonical pair and Top3-subset incidence matrices using sorted runner
   numbers. Validate complete pair roster and `sum(q_m)=1` for all 481 races.
2. Record necessary pair and horse marginal violations. Solve the exact
   `A*pi=3*q_m, pi>=0` feasibility LP with HiGHS and independently verify the
   returned vector.
3. For every non-verified LP result, use only fixed SLSQP settings and its
   supplied analytic gradient to minimize `D_KL(q_m || A*pi/3)` from uniform
   Top3-set mass. Fail closed on solver or verification failure.
4. Retain `pi` diagnostics and `q_star`, and verify joint feasibility,
   probability bounds, and deterministic ordering.
5. Only then read development winning-pair labels, exclude any special labels
   from CE cost, run the fixed block bootstrap, and compare cost scale to
   frozen D1. No outcome enters the projection construction.

## Exclusions

- No August data, calibration/gamma change, model fitting, D1 retraining,
  Market/Policy/WIDE_OPS/live-code change, binary comparison against invalid
  `3*q_m`, or economic/ROI analysis.
- No entropy lift, J1 offset, beta fit, or Top3-set model is constructed.

## Outputs and acceptance

Write the required projection Parquet/JSON artifacts and a provenance run
manifest under `audit/data/p2_wide_j0_projection_audit_20260825/`. Before
projection results are inspected, persist the immutable J1 preregistration
artifact. Completion requires exact 481/29,136 source coverage, zero roster
mismatches, bounded/normalized projected mass, retained feasible `pi`, source
immutability, and targeted mathematics/leakage tests.
