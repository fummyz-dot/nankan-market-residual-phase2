# P2-WIDE-PROSPECTIVE-FREEZE-V1-001 — prospective WIDE research freeze

## Job metadata

- Status: COMPLETE
- Scope: development artifact creation only; no LIVE integration.

## Inputs

- frozen M0 / gamma contract: `audit/data/p2_wide_sci_baseline_20260825/`;
- frozen D1 feature/trainer contract: `audit/data/p2_wide_sci_direct_20260825/`;
- uncertainty V0 primitives: `src/audit/p2_wide_market_uncertainty_v0.py`;
- J0-FS primal-dual solver: `src/audit/p2_wide_j0_fs_primal_dual.py`;
- J1 outer OOF predictions and beta contract:
  `audit/data/p2_wide_j1_d1_joint_20260825/`.

## Procedure and invariants

1. Audit all final-training candidates before fitting: Primary, development
   date no later than 2026-07-31, market/label/FS04/roster complete. No August
   outcome/result input is opened.
2. Fit one M0 lower-only gamma and its fixed B=2000 calendar-date bootstrap
   on the complete final eligible training set. Preserve the existing display
   model and p95 Delta rule; snapshot timing uncertainty remains unavailable.
3. Derive `final_best_iteration = floor(median(b1,b2,b3)+0.5)` from the
   frozen J1 outer D1 fits, then train one D1 full-development model with that
   fixed iteration and the existing 356-feature contract. No full-data early
   stopping or hyperparameter selection occurs.
4. Fit one prospective beta using only frozen 481 outer OOF J0/D1 rows and
   the registered grid plus bounded refinement. Never use full-data D1
   in-sample scores for beta.
5. Write a hash-manifested model bundle at
   `models/development/wide_prospective_v1/`, freeze the prospective protocol
   before the bundle artifact, and run a fresh-process three-race
   prediction-only reproduction.

## Failure / idempotency

- Any cutoff, duplicate, missing feature/market, roster, hash, solver, or OOF
  boundary violation fails closed; no partial bundle is promoted.
- Stage files are written atomically. Re-run with identical final bytes is an
  idempotent verification; differing pre-existing bundle content blocks.

## Acceptance

- final gamma/bootstrap, J0-FS and J1 manifests, D1 binary/feature/training
  manifests, OOF-only beta, confirmation protocol, and bundle hash manifest;
- no August outcome access, no production/live/policy change, no production
  DB mutation; fresh-process prediction-only smoke passes.

## Completion

- Frozen bundle: `models/development/wide_prospective_v1/`.
- Final gamma used 833 development races; D1 used the same 833 races and the
  fixed median-derived iteration 2. Beta used only the 481 outer OOF races.
- The prospectively frozen J0-FS is a market-joint baseline. J1 is explicitly
  `NOT_PROMOTED` and research-shadow-only; neither is a recommendation or
  stake input.
