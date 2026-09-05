# Codex Job Plan

## Job metadata
- Job ID: P2-A01
- Title: Historical Semantic & Class Foundation Audit
- Status: COMPLETED
- Owner: Codex

## Objective
Produce a source-backed, read-only audit of NAR class/condition, pace/corner, cross-venue history, and Keibabook semantics that defines only draft contracts and future feasibility gates.

## Why this job exists
Phase 2 cannot safely construct class, pace, first-3F, cross-venue, or external-data features until raw grain, availability, schema drift, and exclusions are evidenced.

## Allowed inputs
- `reference/v1/db/nankan_history.sqlite` opened SQLite read-only
- `reference/v1/data/raw_nar/zips/race/` opened read-only
- `reference/v1/data/keibabook_samples/` opened read-only
- `reference/v1/docs/`, `reference/v1/tools/`, Phase 2 policy documents, and P2-A00 manifests

## Read-only inputs
- All of `reference/v1/`
- `/home/nabe/projects/nkDb-pro/`

## Allowed modifications
- `.agent/PLANS/P2-A01_historical_semantic_class_audit.md`
- `src/audit/`, `tests/unit/`, `notebooks/`
- `configs/features/`, `docs/`
- `audit/data/p2_a01/`, `reports/development/`, `data/manifests/`

## Forbidden actions
- no writes to V1 original or `reference/v1/`;
- no model fitting, feature-effectiveness testing, ROI, market residual, odds-band, or venue performance evaluation;
- no empirical class-strength calculation or class-strength implementation;
- no inference of unavailable timestamps, runner corner positions, first-3F, or Keibabook subjective/market values;
- no same-day bias as a primary feature.

## Tasks
1. Verify frozen-reference integrity and profile Nankan `conditions_raw` by year and venue.
2. Extract observed condition tokens/patterns, year/venue regime-change candidates, and prize/age/sex/race-type/grade relationships.
3. Produce a non-ordinal canonical mapping DRAFT plus `P2_CLASS_RULE` schema and the two registered future ablation candidates only.
4. Profile lap/corner JSON schemas, determine runner-corner and first-3F reconstructability, and classify Keibabook QA feasibility.
5. Scan all raw NAR venues, quantify South Kanto versus all-venue horse-history coverage, and separate completeness audit from future `P2_XVENUE` modeling.
6. Classify Keibabook fields, codify same-day-bias prohibition, generate artifacts/manifests/report, and run tests plus leakage checks.

## Required artifacts
- Roadmap artifacts: `CLASS_RAW_PROFILE.csv`, `CLASS_CANONICAL_MAPPING_DRAFT.csv`, `CLASS_SYSTEM_VERSION_AUDIT.csv`, `LAP_SCHEMA_PROFILE.json`, `CORNER_SCHEMA_PROFILE.json`, `NAR_KB_JOIN_AUDIT.csv`, `PACE_SOURCE_COMPARISON.csv`, `PHASE2_FEATURE_FEASIBILITY.csv`
- Draft policy/config documents for P2 class, P2 cross-venue boundary, and same-day bias
- P2-A01 audit outputs, run manifest, reproducible notebook, and development report

## Tests / acceptance criteria
- Source hashes before/after and SQLite `quick_check` match P2-A00 reference state.
- All Nankan profiles use the declared raw-corpus period (2020-01-01 through 2026-07-31) and split Ohi/Funabashi/Kawasaki/Urawa; any later DB rows are reported and excluded pending provenance resolution.
- Raw 14-venue archive audit reports observed coverage without treating it as an approved model input.
- No computed class-strength/ROI/prediction/performance outputs exist.
- First-3F and runner-corner feasibility conclusions link to stored raw/schema evidence.
- Keibabook prohibited fields remain classified as prohibited and are not emitted into feature tables.

## Leakage and temporal checks
- This audit reads post-race information only to determine historical semantic feasibility; it does not create model-ready current-race features.
- Any future historical feature must satisfy `available_at <= decision_time`; publication time remains unestablished here.
- Historical same-day bias is explicitly primary-prohibited pending a separately approved contract.

## Run manifest requirements
- `vcs_mode: none`; `git_commit: null`; workspace root and UTC timestamp
- code/input/config manifest hashes; environment versions; `random_seed: null`; commands; output hashes

## Completion report
State the evidence-backed class/pace/cross-venue feasibility gates, draft-only decisions, blocked semantics, files produced, validation result, and the next safe job.
