# P2-WIDE-J0-FS-PRIMAL-DUAL-001 — deterministic regularization-path solver

## Job metadata

- Job ID: P2-WIDE-J0-FS-PRIMAL-DUAL-001
- Title: J0-FS deterministic regularization-path + primal-dual Newton solver
- Status: COMPLETE
- Owner: Codex

## Objective

Solve the already frozen J0-FS maximum-entropy reconstruction exactly under
the existing per-race Market-distortion budget.  This task replaces only the
failed numerical procedure: fixed-kappa equality-constrained primal Newton,
deterministic kappa bracketing/root solve, and final constrained KKT Newton.
No scientific input, constraint, or outcome-dependent choice changes.

## Inputs / outputs

Read-only authority inputs:

- frozen M0 pair mass from `p2_wide_sci_baseline_20260825`;
- `d_min`, `pi_star`, pair/subset ordering from projection;
- frozen `Delta_r`, `B_r`, and strict interpolation witness coordinate from
  Market uncertainty artifacts.

Write only `audit/data/p2_wide_j0_fs_primal_dual_20260825/`, with registered
path/KKT diagnostics, joints/marginals, post-construction evaluation, the
failed-race regression, gate manifest, and gitless run manifest.

## State transitions and numerical contract

1. Validate all authority row counts, keys, q/pair/subset ordering, and exact
   budget equality without label reads.
2. Run the required 2026-05-01 Ohi 6R regression first.  Uniform is checked
   analytically; otherwise solve the fixed-kappa equality Newton path,
   bracket/root kappa, then apply registered constrained-KKT polish.
3. Only after that race passes, solve the remaining 480 races identically;
   all legal Top3 sets must have strict positive mass before labels are read.
4. Only after 481 construction succeeds, read development labels for the
   registered reconstruction diagnostics; never access August outcomes.
5. Atomically promote all artifacts and record SHA-256 provenance. Any inner,
   path, KKT, positivity, or authority failure writes `FAILED` artifacts and
   stops before outcome evaluation.

## Invariants and exclusions

- No `trust-constr`, SLSQP, CVXPY, q-star reprojection, Delta/gamma refit,
  uncertainty tuning, D1/J1, LIVE/WIDE_OPS/Policy change, or production DB
  mutation.
- Fixed-kappa Newton has sum equality only and uses the specified exact
  derivatives/indefinite direct linear solve/positivity Armijo line search.
- The outer path begins at kappa=1, doubles at most 60 times, checks monotonic
  distortion at `1e-10`, then uses at most 80 geometric bisections.
- Final active constrained KKT polish has at most 30 iterations and enforces
  strict positivity, nonnegative kappa, and decreasing squared residual merit.
- A closest-kappa warm start that exhausts its registered Newton/Armijo budget
  may restart the identical fixed-kappa Newton equation from exact uniform,
  then from the already-frozen strict witness; each use is recorded in the
  path audit and changes no scientific input or numerical acceptance threshold.

## Tests / acceptance

- Fixed-kappa gradient/Hessian finite differences; synthetic inner Newton,
  monotonic path, uniform, active-budget root, KKT polish, full support,
  permutation, and deterministic behavior.
- Actual failed Ohi 6R regression before the 481-race run; two prior
  structural-zero races; input source audit with construction outcome access
  exactly zero and August access zero.
- Final acceptance uses the registered equality/constraint/stationarity/
  complementarity/entropy-vs-witness thresholds without relaxation.

## Run manifest

Include `vcs_mode: none`, `git_commit: null`, hashes of source/plan/authority
files/artifacts, platform/library versions, deterministic solver settings, and
hard boundary audits.
