# Codex Job Plan

## Job metadata
- Job ID: `P2S_JOB_003_MATERIALIZED_FEATURE_FOUNDATION`
- Status: COMPLETE
- Owner: Codex

## Objective
Materialize only the B0 and deterministic Primary features frozen in `MATERIALIZED_FEATURE_CONTRACT_V1` for the Job002 eligible universe, using daily strict-as-of state and no model fitting.

## Inputs
- `reference/v1/db/nankan_history.sqlite`, opened only with SQLite `mode=ro`
- Job002 eligible-universe summary and frozen source contracts/manifests
- Local, already-frozen P2 class/speed/pace *semantic* implementations only where consistent with the supplied Materialized Feature Contract.

## Outputs
- `data/manifests/successor_v1/` frozen feature manifests and contract JSON.
- Fixed-format `data/processed/successor_v1/*.csv.gz` datasets.
- `audit/successor_v1/job003/` required audits, hashes, report, and run manifest.
- Builder and focused temporal guard tests under `src/audit/` and `tests/unit/`.

## Invariants
- Exact target race/runner counts: 21,560 / 246,709; `(race_key, horse_number)` unique.
- Target date `<= 2026-07-31`; any later target fails.
- Day D feature rows are locked before any Day D result update. All result-derived sources have date `< D`.
- No market/payout, current outcome, current dynamic body/weather/going, `first_seen_date`, or `last_seen_date` dependency.
- No EB or model/PL/calibration/ROI/threshold fitting.

## Materialization sequence
1. Load all South-Kanto source races, identify the Job002 target universe, and group by date.
2. For each date: compute target feature rows from historical state, aggregate race composition from those pre-race runner states, write rows, then update histories from that date's completed source races.
3. Build strict-as-of speed standards before the date's speed observations are calculated; audit center, scale, sample count, fallback, and fitted-through date.
4. Validate counts, hashes, temporal audits, prohibited-source scans, missingness/cold-start coverage, and fixed feature order.

## Exclusions
No source DB changes, no existing artifact overwrite, no external access, live collector, model fitting, or feature selection.

## Completion record
- `attempt_001` was quarantined under `audit/successor_v1/job003/attempts/attempt_001_incomplete/` with a pre-move SHA-256 inventory.
- `attempt_002` wrote seven year partitions to staging, validated every checkpoint, then atomically promoted both dataset directories.
- Final status: `JOB003_PASS_WITH_WARNINGS`; all required hard acceptance counts and zero-violation audits passed.
