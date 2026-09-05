# P2-A02B-3 — End-to-End Race Analysis Bundle Foundation

## Job metadata
- Job ID: P2-A02B-3
- Title: End-to-End Race Analysis Bundle Foundation
- Status: COMPLETED
- Owner: Codex

## Objective
Create one immutable, machine-readable `p2_race_analysis_bundle_v1` from the retained 2026-08-19 川崎5R T-15 capture and daily Keibabook JSON, without model or wagering work.

## Allowed inputs
- `outputs/live_freshness/2026-08-19/川崎_race05_live_freshness.json`
- `db/market_snapshot.sqlite`
- `data/raw/keibabook/inbox/2026-08-19/`
- `audit/data/p2_a01/KEIBABOOK_FIELD_CLASSIFICATION.csv`
- Phase 2 contracts and reports named in the job request.

## Read-only inputs
- `reference/v1/` and all V1 original assets.
- Saved live captures and Keibabook inbox raw JSON (read-only inputs to this job).

## Allowed modifications
- `src/operations/`, `src/audit/`, `tests/`, `docs/`, `outputs/analysis_bundles/`, `audit/data/p2_a02b3/`, `reports/development/`, `data/manifests/`, and this plan.

## Invariants
- Select only the explicit `PRIMARY_CANDIDATE` / T-15 capture; never select latest capture.
- Keep Main, Keibabook objective (`P2X_O`), and training (`P2X_S`) structurally separate.
- Include no current-race result, payout, post-T15 snapshot, probability, edge, or ticket candidate.
- `race_name` is nullable; `conditions_raw` remains separate.
- Exact Keibabook race resolution and horse-number joins only; no fuzzy matching.

## Tasks
1. Define bundle and eligibility draft contracts and the non-frozen daily-operation baseline.
2. Implement deterministic retained-capture bundle builder, daily Keibabook discovery, exact resolution, allow-list sanitization, and provenance.
3. Generate the 川崎5R bundle and audit T-15 selection, joins, boundary, prohibition, eligibility, schema, and hash.
4. Add unit/integration/leakage tests and report results.

## Required artifacts
- `outputs/analysis_bundles/2026-08-19/川崎_race05_analysis_bundle.json`
- all requested `audit/data/p2_a02b3/` CSV/manifest files
- `reports/development/P2_A02B3_END_TO_END_ANALYSIS_BUNDLE_REPORT.md`

## Tests / acceptance criteria
- T15 only, no post-primary market input, and T15 capture/hash match DB.
- 11 bodyweight/WIN runners; 55 WIDE pairs; 165 TRIO combinations.
- uniquely resolve both Keibabook race blocks; report exact horse-number joins.
- validate source boundary, prohibited-field removal, eligibility, absence of results/payouts, schema, and bundle immutability.

## Process supervision
- Foreground, synchronous execution only. No background workers or child processes.

## Run manifest requirements
- `vcs_mode: none`, `git_commit: null`, SHA-256 code/input/config/output manifests, environment metadata, `random_seed: null`, commands, and artifact hashes.

## Completion
- Generated the retained-input 2026-08-19 川崎5R bundle with explicit T15 selection.
- T10/T05 were present and excluded; bodyweight/WIN/WIDE/TRIO completeness and exact Keibabook joins passed.
- No network, model, ticket, result, payout, background, or V1 write was used.
