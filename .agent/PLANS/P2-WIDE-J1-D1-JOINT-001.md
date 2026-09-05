# P2-WIDE-J1-D1-JOINT-001 — cross-fitted D1 residual joint offset

## Job metadata

- Job ID: P2-WIDE-J1-D1-JOINT-001
- Title: Cross-fitted D1 residual → 1-parameter joint-consistent WIDE J1
- Status: COMPLETE
- Owner: Codex

## Objective

Using only development-period data, test the registered one-parameter
joint-consistent tilt of frozen full-support J0-FS by cross-fitting the
already frozen D1 FS04-pair residual direction.  The output is a development
screen only; it cannot promote or alter LIVE WIDE_OPS, Policy, Market,
uncertainty, D1 feature semantics, or J0-FS.

## Authority inputs and outputs

Read-only authorities:

- calibrated M0 / outer walk-forward contract:
  `audit/data/p2_wide_sci_baseline_20260825/`;
- D1 pair contract and frozen LightGBM configuration:
  `audit/data/p2_wide_sci_direct_20260825/`;
- display-plus-gamma uncertainty V0:
  `audit/data/p2_wide_market_uncertainty_v0_20260825/`;
- frozen full-support outer J0-FS joints:
  `audit/data/p2_wide_j0_fs_primal_dual_20260825/`.

Write only `audit/data/p2_wide_j1_d1_joint_20260825/`, the new audit module,
targeted tests, and this plan.  The new module will use the established D1
pair builder/trainer, M0 gamma fitter, uncertainty primitives, projection,
and J0-FS primal-dual solver rather than creating parallel model semantics.

## State transitions and outcome boundary

1. Verify frozen authority hashes, the outer 481-race / 29,136-pair key set,
   D1 alignment, J0-FS full support, and outer fold boundaries without reading
   outer validation labels.
2. For each outer fold, create the registered JST calendar-month
   rolling-origin inner folds from its training period.  Every inner market
   gamma, uncertainty bootstrap, projection, and J0-FS construction is based
   only on its preceding inner-training dates.  Generate D1 predictions only
   for the corresponding inner-validation month.
3. Require at least 80 inner-OOF races and one inner fold; otherwise fail
   `J1_BETA_TRAINING_INSUFFICIENT` rather than changing the outer sample.
4. Fit exactly one nonnegative beta from inner-OOF rows using the registered
   81-point grid and local bounded refinement.  Then train D1 once on each
   full outer-training period and generate only its outer out-of-time score.
5. Apply each training-frozen beta to the frozen outer J0-FS joint.  Finish
   construction and all full-support/probability audits for all 481 outer
   races before opening their labels for Pair CE, Set NLL, binary log loss, or
   Brier evaluation.
6. Atomically write all registered artifacts and a gitless run manifest.  A
   source, OOF, roster, numerical, or guardrail invariant failure ends in a
   failed audit artifact; no output silently replaces a frozen authority.

## Invariants and exclusions

- J1 uses `f=log(q_D1/q_market)`, pair-centering, and P0-centering of the
  three-pair subset statistic.  `beta=0` is exactly frozen J0-FS.
- Inner D1 predictions are OOF; outer D1 predictions are out-of-time; beta
  fitting reads only inner-OOF labels.  Outer outcomes are evaluation-only.
- Each inner uncertainty bootstrap retains B=2000, the existing deterministic
  seed contract, display model, gamma procedure, p95-Delta rule, and the
  frozen J0-FS numerical contract.  No fold substitutes its outer gamma or
  Delta.
- No August outcome/result access, model or hyperparameter search, PL/range
  feature, extra joint parameter, J0/Market/Delta change, live code/policy
  change, economic analysis, or production DB mutation.

## Tests / acceptance

- Unit tests cover beta-zero identity, subset and pair normalization,
  centering/permutation invariance, inner OOF temporal boundaries, beta grid
  determinism, and no validation-label use during inner gamma/Delta/J0
  construction.
- A fresh-process reduced WF fixture must exercise inner walk-forward → gamma
  → uncertainty → J0-FS → D1 OOF → beta → outer D1 → outer J1.
- Full run requires exactly 481 outer races and 29,136 canonical pairs,
  strict J0/J1 support, zero roster mismatch, all probability sums, the
  registered bootstrap, and source/hash/mutation audits.

## Completion

- Full outer OOF construction completed on the frozen 481-race / 29,136-pair
  authority set. The result is `NO_J1_SIGNAL`: mean Pair CE is worse than
  J0-FS, so J1 is not a development candidate despite the specified Set NLL
  and binary guardrails passing.
- The evaluation loop now binds each row's race key before retrieving its
  outcome label. A targeted regression test prevents stale-key assignment
  across adjacent races.
- Fresh-process reduced smoke and the relevant 33-unit-test group passed;
  August outcome access, result DB access, production DB mutation, LIVE
  changes, and Policy changes are all zero.

## Run manifest

Record `vcs_mode: none`, `git_commit: null`, workspace root, source/authority
and output hashes, platform/library versions, random seeds, commands, solver
and beta-search contracts, explicit outcome-access counters, and known
limitations.
