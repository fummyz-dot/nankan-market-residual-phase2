# Codex Job Plan

## Job metadata
- Job ID: P2S_JOB_002_FEATURE_DATA_FOUNDATION_CONTINUE
- Title: Feature / Training Data Foundation
- Status: COMPLETE
- Owner: Codex

## Objective
Create frozen successor-V1 source/contract foundations, enforce temporal/dependency guards, and audit the eligible development universe without training a model.

## Allowed inputs
- `data/manifests/feature_source_adjudication_v1.csv`
- `audit/data/p2s_fac_a001_source_usage_semantics/validation.json`
- `reference/v1/db/nankan_history.sqlite` opened only with `mode=ro`
- `data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json`
- Existing outcome/target-universe contracts and read-only tests.

## Read-only inputs
- All existing G0, reference/V1, DB, model, collector, and feature artifacts.

## Allowed modifications
- `docs/successor_v1/`
- `data/manifests/successor_v1/`
- `audit/successor_v1/job002/`
- `src/audit/p2s_job002_feature_data_foundation.py`
- `tests/unit/test_p2s_job002_feature_data_foundation.py`
- This job plan.

## Forbidden actions
- Model/GBDT/BLUP/PL/calibration fitting; threshold or ROI work; collector/network/paid-data activity; modification of G0 or source adjudication; DB writes.

## Tasks
1. Encode supplied feature/training data contracts in Markdown and JSON.
2. Create B0 and Primary source manifests solely from the supplied adjudication.
3. Implement temporal/dependency guards and negative-control unit tests.
4. Count the cutoff-bounded, four-venue development universe and requested outer folds.
5. Map all 178 FS04 semantics without authorizing existing materialized values.

## Required artifacts
- Every file listed in Job 002 §12, plus the four contract artifacts in §4.

## Tests / acceptance criteria
- Adjudication validation is PASS and has 106 rows.
- All requested guard controls pass as expected, with prohibited controls rejected.
- No target race after 2026-07-31 is present in the counted universe.
- B0 and Primary manifests have no direct market/current-outcome/blocked metadata dependency.
- FS04 map has exactly 178 unique features.

## Leakage and temporal checks
- Result-derived sources require strict prior calendar date; same-day is false everywhere.
- Post-cutoff source rows are counted as source-corpus evidence only, never made development targets.

## Process supervision
- One foreground, synchronous, bounded job; no workers.

## Run manifest requirements
- `vcs_mode: none`, `git_commit: null`, code/input/config/output hashes, platform/library versions, commands, artifacts, and `random_seed: null`.

## Completion record
- Completed with `JOB002_PASS_WITH_WARNINGS`.
- Executed: `python3 -m unittest tests.unit.test_p2s_job002_feature_data_foundation` (5 tests PASS) and the Job 002 artifact self-check (required artifacts, 29 guard rows, hashes, false-operation flags, and zero post-cutoff targets PASS).
- Warnings retained in `audit/successor_v1/job002/issues.csv`: 128 post-cutoff source-corpus rows are recorded but not target rows; the historical starter universe is not a T15 roster-equivalence claim.
