# P2-WIDE-J0-MAXENT-DUAL-POLISH-001 — J0 dual Newton polish

## Job metadata

- Job ID: P2-WIDE-J0-MAXENT-DUAL-POLISH-001
- Title: J0 dual MaxEnt deterministic Newton polish
- Status: BLOCKED
- Owner: Codex

## Objective

Keep the frozen J0 support face and projected `q_star` unchanged, then use the
specified deterministic Newton polish after the fixed L-BFGS-B dual solve to
meet the existing full-marginal acceptance gates on all 481 development races.

## Why this job exists

The pre-registered support-face dual construction reached the correct face but
the fixed L-BFGS-B stopping rule left numerical residuals above the unchanged
hard tolerances on a required regression race.  This is solver engineering,
not a Market or model experiment.

## Allowed inputs

- `audit/data/p2_wide_j0_projection_audit_20260825/projection_race_results.parquet`
- `audit/data/p2_wide_sci_baseline_20260825/fold_predictions.parquet`, opened
  only after all 481 outcome-free constructions pass
- frozen baseline/projection manifests

## Read-only inputs

- All projection and baseline audit artifacts above
- `reference/v1/` and every production/live database

## Allowed modifications

- `src/audit/p2_wide_j0_maxent_dual.py`
- `tests/unit/test_p2_wide_j0_maxent_dual.py`
- Phase 2 audit outputs under
  `audit/data/p2_wide_j0_maxent_dual_polish_20260825/`

## Forbidden actions

- No `q_star` reprojection, smoothing, support redefinition, Market change,
  model change, J1/D1 work, or live-code changes.
- No outcome access until all 481 constructions pass.
- No tolerance relaxation, alternative optimizer, extra solver search, or
  production database mutation.

## Tasks

1. Retain the existing LP support discovery and L-BFGS-B dual as the first
   stage; add the specified analytic-Hessian Newton polish only for residual
   failures.
2. Run the pre-registered 2026-05-07 Funabashi 3R regression first, verifying
   the frozen support and `q_star` before all-race construction.
3. Run the 481-race outcome-free construction, then outcome-only J0 metrics
   and structural-zero gate.
4. Persist solver diagnostics, manifests, and regression artifacts.

## Tests / acceptance criteria

- Exact Hessian finite-difference, symmetry, and PSD tests.
- Newton objective non-increase, boundary/full-support, permutation, and
  deterministic rerun tests.
- Regression: 2026-05-07 Funabashi 3R passes unchanged support with gradient
  `<=1e-9` and full marginal residual `<=1e-8`.
- All 481 construction records pass before labels are opened.

## Leakage and temporal checks

- Construction reads only the outcome-free projection artifact.
- Development labels are opened only after construction is complete.
- August outcome/result access, result DB access, and production DB mutation
  remain zero.

## Process supervision

- Foreground, synchronous, deterministic execution; no child workers.

## Run manifest requirements

- `vcs_mode: none`, `git_commit: null`, source/input hashes, environment,
  command, seed `null`, and artifact hashes.

## Completion report

Report solver coverage, direct versus polished counts, residual maxima,
required-regression before/after values, structural-zero result, and unchanged
research/live boundaries.

## Outcome

- The deterministic Newton polish passed all 481 numerical constructions and
  the required 2026-05-07 Funabashi 3R regression without changing `q_star`.
- Outcome labels were opened only after construction. Two true Top3 sets are
  outside their pre-outcome structural support, so the required hard gate is
  `J0_MAXENT_SUPPORT_BLOCKED`; no J1 work follows from this job.
