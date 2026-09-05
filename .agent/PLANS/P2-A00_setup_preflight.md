# Codex Job Plan

## Job metadata
- Job ID: P2-A00
- Title: Setup Preflight & Workspace Normalization
- Status: COMPLETED
- Owner: Codex

## Objective
Audit and normalize the Phase 2 workspace, safely complete the permitted V1 reference copy, and produce immutable SHA-256 provenance artifacts without performing research analysis.

## Why this job exists
P2-A01 must start from an auditable Phase 2/V1 boundary, intact reference assets, and a Gitless local provenance policy.

## Allowed inputs
- `AGENTS.md`, `README.md`, `docs/`, `.agent/`, `src/`, `tests/`, `configs/`
- `/home/nabe/projects/nkDb-pro/` (read-only V1 original)
- `reference/v1/` only until its immutable lock is applied

## Read-only inputs
- `/home/nabe/projects/nkDb-pro/`
- V1 source DBs, tools, documents, archives, processed references, and Keibabook samples

## Allowed modifications
- `AGENTS.md`, `docs/CODEX_WORKFLOW.md`, `.agent/CODEX_JOB_TEMPLATE.md`
- `.agent/PLANS/P2-A00_setup_preflight.md`
- `docs/WORKSPACE_POLICY.md`, `docs/PROJECT_STATE.md`, `docs/DECISIONS.md`
- Missing active namespace directories and `.gitkeep` files
- Missing `reference/v1/` copies, reference manifests, and its final permissions
- `src/audit/`, `tests/unit/`, `data/manifests/`, `audit/setup/p2_a00/`, `reports/development/`

## Forbidden actions
- no writes to V1 original repository or DBs;
- no model training, tuning, feature-effectiveness, ROI, venue, odds-band, or holdout evaluation;
- no semantic inference for leakage-sensitive fields;
- no `git init` or Git-required workflow;
- no modification of `reference/v1/` after immutable lock.

## Tasks
1. Verify paths and read required policy/template documents.
2. Establish Gitless SHA-256 provenance language and Phase 2 governance documents.
3. Audit/create active namespace directories.
4. Compare allowed V1 assets by basename/path; copy only missing assets with hash verification, using SQLite backup only when a DB is missing.
5. Audit DB integrity/counts, raw archive coverage, Keibabook sample structure/exclusions, symlinks, and permissions.
6. Write reference/code manifests, setup audit artifacts, final report, and lock `reference/v1/` read-only.
7. Run deterministic unit and artifact-consistency checks, then issue the readiness decision.

## Required artifacts
- `reference/v1/manifests/V1_REFERENCE_MANIFEST.{csv,json}`
- `data/manifests/PHASE2_CODE_MANIFEST.csv`
- all required `audit/setup/p2_a00/` files
- `reports/development/P2_A00_SETUP_PREFLIGHT_REPORT.md`

## Tests / acceptance criteria
- both V1 SQLite DBs return `PRAGMA quick_check = ok`;
- expected structural counts are either matched or explicitly classified;
- V1 Python tool basename parity and required V1 contracts hold;
- raw archive coverage and Keibabook sample requirements hold;
- generated manifests and report exist with SHA-256 provenance;
- `reference/v1/` has no V1-original symlink and is non-writable after lock.

## Leakage and temporal checks
- No model-ready datasets, labels, performance metrics, or feature generation are created.
- `MARKET_TIME_UNKNOWN` data remains a reference only; `odds_snapshots = 0` is recorded as expected.
- Keibabook market/prediction fields are inspected only as excluded fields.

## Run manifest requirements
- `vcs_mode: none`; `git_commit: null`
- workspace root, creation timestamp, SHA-256 code/input/config manifests
- Python/platform/library versions, `random_seed: null`, commands, and artifact hashes

## Completion report
Summarize changed files, copied V1 assets, DB checks, missing items, warnings, lock status, and P2-A01 decision.
