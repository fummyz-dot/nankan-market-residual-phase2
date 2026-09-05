# Codex Job Plan

## Job metadata
- Job ID: `JOB006`
- Status: COMPLETE_JOB006_PASS

## Inputs
- Frozen Stage2 authority supplied in JOB006.
- Accepted JOB005A commit `28ccc29a7e15f320d50e3ff84d6d4a31869e6993`.
- Read-only Job004 audit/checkpoint lineage and immutable history DB.
- Existing strict-as-of history, prospective roster, and feature-builder source.

## Outputs
- Frozen Stage2 JSON/Markdown authority.
- A deterministic, outcome-blind Fold4 artifact/readiness inventory executable and tests.
- Local audit artifacts under `audit/successor_v1/job006/`.
- Sanitized design and evidence under `docs/`.

## Invariants and exclusions
- Do not read prospective outcomes, payouts, settlements, or Stage2 performance.
- Do not fit models, probabilities, calibration, or economic metrics.
- Continue exact Fold4 M2/M1 lineage; never substitute the legacy 178-feature live model.
- Require exact ordered feature counts/hashes and date-causal EB semantics.
- Model binaries, DBs, raw data, processed data, and runtime audit outputs remain untracked.

## Acceptance tests
- Synthetic artifact path/hash validation and exact M2/M1 lineage checks.
- Prospective outcome table/path guard.
- Dynamic EB unseen-key zero behavior and same-day no-update rule.
- Exact Primary129 and RaceHead32 feature-hash enforcement.
- Real read-only inventory resolves required Fold4 models/components and emits readiness classifications.
