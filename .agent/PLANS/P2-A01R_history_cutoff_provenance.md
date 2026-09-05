# Codex Job Plan

## Job metadata
- Job ID: P2-A01R
- Title: History Cutoff Provenance Resolution
- Status: COMPLETED
- Owner: Codex

## Objective
Establish the origin and source-trace status of every `nankan_history.sqlite.races` row dated after 2026-07-31, while proving whether the declared historical-development aggregate is structurally isolated.

## Why this job exists
P2-A01 detected 128 database races outside the locked raw-corpus period. Their provenance must be classified without changing the Phase 2 development cutoff.

## Allowed inputs
- `reference/v1/db/nankan_history.sqlite` opened read-only
- `reference/v1/data/raw_nar/` and `reference/v1/manifests/` opened read-only
- `/home/nabe/projects/nkDb-pro/` read-only only when it supplies provenance evidence
- P2-A01 outputs and Phase 2 policy documents

## Read-only inputs
- All `reference/v1/`
- All V1-original paths

## Allowed modifications
- `.agent/PLANS/P2-A01R_history_cutoff_provenance.md`
- `src/audit/`, `tests/unit/`, `audit/data/p2_a01r/`, `reports/development/`
- `docs/PROJECT_STATE.md` and `docs/DATA_SOURCE_POLICY.md` only for an evidence-backed cutoff policy clarification
- `data/manifests/`

## Forbidden actions
- no V1 original/reference writes;
- no model training, performance/ROI/feature-effectiveness evaluation;
- no cutoff extension or provenance inference;
- no use of the 128 rows in a historical-development aggregate.

## Tasks
1. Extract and summarize all post-cutoff races and reconcile race versus runner `source_month`.
2. Trace raw ZIP/member candidates and their manifest/hash provenance for every race.
3. Classify every row using the requested five-class taxonomy without filling gaps.
4. Audit structural backward contamination via race/runner keys, imports, and horse-history aggregation boundaries.
5. Write the immutable cutoff policy conclusion, artifacts, report, manifest, and tests.

## Required artifacts
- `audit/data/p2_a01r/post_cutoff_128_races.csv`
- `audit/data/p2_a01r/post_cutoff_date_venue_summary.csv`
- `audit/data/p2_a01r/post_cutoff_source_trace.csv`
- `audit/data/p2_a01r/post_cutoff_classification.csv`
- `audit/data/p2_a01r/cutoff_isolation_audit.csv`
- `audit/data/p2_a01r/data_quality_issues.csv`
- `audit/data/p2_a01r/run_manifest.json`
- `reports/development/P2_A01R_HISTORY_CUTOFF_PROVENANCE_REPORT.md`

## Tests / acceptance criteria
- Exactly 128 post-cutoff races are extracted and each has one classification.
- Source trace checks the ZIP/member corpus and manifest hash for every row.
- Race/runner source-month, imports, and key overlap checks are explicit.
- The cutoff remains 2026-07-31 regardless of provenance outcome.
- Source hash/SQLite quick-check remains unchanged.

## Leakage and temporal checks
- The audit uses post-cutoff rows solely for provenance classification, never feature construction or model evaluation.
- The pre-cutoff aggregate is represented by `race_date <= 2026-07-31`; source-month alone is not used to enlarge it.

## Run manifest requirements
- `vcs_mode: none`, `git_commit: null`, root/timestamp, code/input/config manifests, environment, null seed, commands, and artifact hashes.

## Completion report
State exit status, root cause evidence, classification totals, backward-contamination result, unchanged policy, files, and next safe action.
