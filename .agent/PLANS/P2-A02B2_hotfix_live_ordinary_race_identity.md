# P2-A02B2-HOTFIX — Live Ordinary-Race Identity Parser

## Job metadata
- Job ID: P2-A02B2-HOTFIX
- Title: Live Ordinary-Race Identity Parser
- Status: COMPLETE_WITH_LIVE_RECHECK_REQUIRED
- Owner: Codex

## Objective
Allow the foreground live-freshness bootstrap to resolve a normal conditions race whose page has no distinct race name, while preserving race identity and leakage boundaries.

## Why this job exists
The live probe stopped at bootstrap because the official adapter treated the display title as a required `race_name`. Ordinary conditions races can present only a class/conditions label.

## Allowed inputs
- `src/ingestion/adapters/nankan_official.py`
- `src/operations/live_freshness_probe.py`
- existing historical official fixtures and tests
- one direct foreground fetch of `https://www.nankankeiba.com/uma_shosai/2026081921060205.do` for parser verification and an immutable raw test fixture

## Read-only inputs
- `reference/v1/`
- all V1 original assets

## Allowed modifications
- `src/ingestion/adapters/`
- `src/operations/`
- `tests/unit/`
- `data/raw/fixtures/nankan_official/`
- `data/manifests/`
- `audit/data/p2_a02b2_hotfix/`
- `reports/development/`
- `docs/PHASE2_DATA_CONTRACT.md`
- this job plan

## Forbidden actions
- no writes to V1 reference/original repo;
- no model training, inference, performance evaluation, ROI evaluation, result retrieval, or bet generation;
- no market or odds-page fetch during the permitted live access;
- no background or child processes;
- no inferred replacement for a page field that is absent.

## Tasks
1. Make `race_name` nullable and preserve a conditions-only official title in `conditions_raw`.
2. Make the live-probe bootstrap response parser reusable without a second fetch.
3. Make the permitted single entry-page fetch; archive/hash if parsing permits it, and add an ordinary-race fixture test without repeating the request.
4. Run the focused and regression tests, then write audit artifacts, run manifest, and report.

## Required artifacts
- `audit/data/p2_a02b2_hotfix/ordinary_race_identity_audit.csv`
- `audit/data/p2_a02b2_hotfix/live_bootstrap_check.csv`
- `audit/data/p2_a02b2_hotfix/data_quality_issues.csv`
- `audit/data/p2_a02b2_hotfix/run_manifest.json`
- `reports/development/P2_A02B2_HOTFIX_LIVE_ORDINARY_RACE_IDENTITY_REPORT.md`

## Tests / acceptance criteria
- named historical fixture still parses with its race name;
- ordinary synthetic fixture parses `race_name = null` and separate `conditions_raw`;
- required canonical identity fields remain mandatory;
- live probe bootstrap parser can resolve the ordinary fixture to race identity with no mark capture; live response recheck is required if the single permitted fetch did not reach parser success;
- all relevant unit, integration, and leakage tests pass.

## Leakage and temporal checks
- The allowed live response is archived solely as an adapter-identity fixture, never as a snapshot, market input, current-race feature, result, or prediction input.
- `T-15` remains `ENGINEERING_CANDIDATE`, not frozen.

## Process supervision
- Foreground, synchronous, bounded execution only.
- No background processes or child workers are permitted for this hotfix.

## Run manifest requirements
- `vcs_mode: none`
- `git_commit: null`
- workspace root and creation timestamp
- SHA-256 code, input, and config manifests
- Python version, platform, and library versions
- `random_seed: null`
- commands and artifact list with output SHA-256 values

## Completion report
Record the race-name root cause, live fetch count, fixture identity result, tests, and the absence of V1 modifications.
