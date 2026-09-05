# P2-WIDE-J0-FS-001 — Uncertainty-budgeted full-support Market joint

## Job metadata

- Job ID: P2-WIDE-J0-FS-001
- Title: Uncertainty-budgeted full-support Maximum Entropy WIDE Market joint
- Status: BLOCKED
- Owner: Codex

## Objective

Construct the frozen `WIDE_MARKET_JOINT_J0_FS_V0` baseline for the existing
481-race / 29,136-pair development common sample.  Every joint must maximize
entropy subject only to the pre-frozen Market-distortion budget and must retain
strictly positive probability for every legal unordered Top3 subset.

## Inputs and authority

- Baseline M0 `q_m` from `p2_wide_sci_baseline_20260825`.
- Projection authority (`d_min`, `pi_star`, subset/pair order) from
  `p2_wide_j0_projection_audit_20260825`.
- Per-race `Delta_r`, `total_budget`, and full-support interpolation witness
  coordinates from `p2_wide_market_uncertainty_v0_20260825`.

All authority files are read-only. The witness is reconstructed exactly as
`(1-t_witness) * pi_star + t_witness * U` and revalidated before a solver is
called.

## State transitions

1. Validate manifests, row counts, pair/subset order, M0 authority, and exact
   `d_min/Delta_r/budget` agreement; write no outcome-derived artifact.
2. For each race, evaluate the uniform joint. If it meets the frozen budget,
   select it as `UNIFORM_FEASIBLE`; otherwise solve the registered convex
   trust-constr formulation from the strict full-support witness.
3. If a general solution misses registered KKT tolerances, apply only the
   registered deterministic active-constraint Newton polish; otherwise fail
   closed as `J0_FS_KKT_FAILED`.
4. After all 481 constructions and full-support checks pass, read development
   labels exactly once for Pair CE, Set NLL, and binary diagnostics. No August
   labels or outcomes are accessed.
5. Atomically write artifacts and a gitless SHA-256 run manifest. Existing
   artifacts are overwritten only after a complete in-memory run; reruns must
   reproduce deterministic output bytes where timestamps are excluded.

## Numerical / failure contract

- `q_m`, `d_min`, `Delta_r`, gamma, and the 95th-percentile rule are never
  recomputed or modified.
- `trust-constr` uses its predeclared analytic objective/constraint
  derivatives, equality sum constraint, `D(pi) <= budget`, and the specified
  options. No alternative optimizer or parameter search is allowed.
- Solver, derivative, witness, positivity, probability, KKT, and full-support
  failures stop the job before outcome reads and produce a failed manifest.
- General solutions require the registered equality, feasibility,
  stationarity, multiplier, and complementarity bounds. Uniform solutions use
  the analytic optimum proof.

## Exclusions

No q-star projection, Delta/gamma recalculation, D1/J1 fitting, model
promotion, LIVE/WIDE_OPS/Policy changes, economic analysis, production DB
mutation, or outcome access during construction.

## Tests / acceptance

- Uniform-feasible identity; interior and previously boundary synthetic cases.
- Objective/KL analytic gradient and Hessian finite-difference checks.
- KKT and budget feasibility, positive all-subset probabilities, permutation
  invariance, deterministic repeat, and known two former structural-zero races.
- Construction source audit proves no validation or August outcome columns are
  read before all 481 joints pass.

## Run manifest

Record `vcs_mode: none`, `git_commit: null`, workspace root, source/input/plan
hashes, Python and library versions, commands, output hashes, and hard audits.

## Blocking result

The fixed `trust-constr` configuration failed before the first construction
could complete: `2026-05-01 大井6R` (12 runners, 66 pairs, 220 subsets) returned
status 4, `Constraint violation exceeds 'gtol'`, after 386 iterations.  The
strict retained witness was valid (`D=0.0009723444982472662 <
budget=0.003343188166137787`, min pi `9.615111571234802e-05`), but the returned
iterate had sum(pi)=1.103087878549153 and D=0.14926731381926392.  Because the
registered Newton polish is permitted only after a successful trust-constr
solve, applying it here or changing solver options/parameterization would
exceed the frozen numerical contract. No validation/August outcome was read.
