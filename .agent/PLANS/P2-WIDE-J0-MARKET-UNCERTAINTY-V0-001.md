# P2-WIDE-J0-MARKET-UNCERTAINTY-V0-001 — Outcome-independent Market uncertainty freeze

## Job metadata

- Job ID: P2-WIDE-J0-MARKET-UNCERTAINTY-V0-001
- Title: J0-FS Market uncertainty budget freeze
- Status: COMPLETE
- Owner: Codex

## Objective

Freeze one outcome-independent per-race `Delta_r` budget for the future J0-FS
constraint, derived only from raw lower-odds display precision and each outer
fold's existing Market gamma calibration uncertainty.

## Allowed inputs

- Frozen baseline, projection, and J0 dual-polish artifacts for the 481-race
  development common sample.
- Historical raw WIDE lower-odds source/provenance required to inspect the
  displayed string before parser float conversion.
- Existing outer-fold training records only for the unchanged gamma objective.

## Read-only inputs

- All authority artifacts and historical raw inputs.
- Validation outcomes, August outcomes, production/live databases, and
  `reference/v1/`.

## Allowed modifications

- `src/audit/p2_wide_market_uncertainty_v0.py`
- targeted unit tests
- `audit/data/p2_wide_market_uncertainty_v0_20260825/`

## Forbidden actions

- No J0-FS fit, q-star projection, Market recalibration rule change, D1/J1,
  outcome-sensitive Delta rule, snapshot-time inference, LIVE change, policy
  change, or production mutation.

## Tasks

1. Audit raw lower-odds display steps for every pair in the common sample.
2. Refit only the frozen gamma estimator on calendar-block bootstrap resamples
   of each outer fold's training races.
3. Generate deterministic display-plus-gamma draws and freeze `Delta_r` as
   the predeclared linear-method 95th percentile of KL divergence.
4. Prove a strictly full-support interpolation witness under every budget and
   record interiority diagnostics without validation-label reads.
5. Persist the J0-FS preregistration before observing any J0-FS fit or
   validation outcomes.

## Tests / acceptance criteria

- Raw display parser accepts only resolved raw strings/steps.
- Frozen `q_m` numerical reproduction; gamma bootstrap respects outer-training
  date boundaries.
- Delta finite and positive; deterministic seed/draw reproducibility.
- Bisection witness is strictly positive and within budget for every race.
- Interiority LP validity, no validation/August outcome access, and rerun
  determinism.

## Leakage and temporal checks

- Validation records are never read by the gamma bootstrap objective.
- All 481 uncertainty and witness calculations are outcome-free.
- Snapshot timing uncertainty is explicitly unavailable for
  `MARKET_TIME_UNKNOWN` history.

## Process supervision

- Foreground, deterministic, synchronous execution; no workers.

## Run manifest requirements

- `vcs_mode: none`, `git_commit: null`, input/code hashes, library versions,
  random seed, commands, and output hashes.

## Completion report

Report display-step coverage, fold bootstrap distributions, Delta and total
budget quantiles, interiority classifications, 481 witness coverage, the two
prior structural-zero race diagnostics, and outcome/live boundary audits.

## Outcome

- All 481 common races resolved lower-odds raw display precision and received
  a strictly positive full-support witness under the pre-frozen Delta rule.
- The artifacts retain no validation outcomes; outer-training outcomes are
  used only by the unchanged fold gamma bootstrap objective.
- J0-FS itself was not fitted by this task.
