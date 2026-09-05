# Codex Job Plan

## Job metadata
- Job ID: P2-A02B-1
- Title: Nankan Official Historical Fixture Adapter
- Status: COMPLETED
- Owner: Codex

## Objective
Create a source-specific official-Nankan adapter from a single historical fixture while permanently separating it from live snapshot evidence.

## Allowed inputs
- The specified 2026-07-31 川崎10R official entry and discovered official odds pages; A02A Phase 2 DB/contracts; local fixture bytes retained from direct WSL fetch.

## Read-only inputs
- `reference/v1/`, V1 original, and Keibabook inbox files.

## Allowed modifications
- `src/ingestion/adapters/`, `src/operations/`, `tests/`, `data/raw/fixtures/nankan_official/`, `data/manifests/`, `db/market_snapshot.sqlite`, `audit/data/p2_a02b1/`, `reports/development/`, and active Phase 2 docs/state.

## Forbidden actions
- No V1 writes, no models/evaluations/ROI/bet generation, no cache-busting query parameters, no live-snapshot claim, no T-15 freeze, and no Keibabook integration.

## Tasks
1. Direct-fetch/archive the designated historical official pages with redirect and HTTP/cache metadata.
2. Implement small parsing/resolution functions with URL/page identity reconciliation and P2_CURRENT allow-list quarantine.
3. Parse and validate WIN, WIDE, and TRIO fixture tables; write only historical-fixture DB records.
4. Add a foreground fixture-safe one-command runner, tests, manifests, audits, and report.

## Required artifacts
- The requested `audit/data/p2_a02b1/` CSV/JSON outputs and `P2_A02B1_NANKAN_OFFICIAL_ADAPTER_REPORT.md`.

## Tests / acceptance criteria
- Identity/page consistency, body-weight quarantine, odds link discovery, odds parses/counts/canonical keys, HTTP metadata, display time, fixture isolation, DB round-trip, and SQLite quick check pass.

## Leakage and temporal checks
- Fixture odds are never recorded as actual pre-race/live or primary-candidate snapshots.
- `availability_status=HISTORICAL_FIXTURE_ONLY`; body-weight output uses the A02A positive allow-list.

## Process supervision
- Foreground synchronous fetches only. No background or child process is permitted for this job.

## Run manifest requirements
- Gitless provenance with SHA-256 input/code/config/output manifests, environment, commands, and null seed.

## Completion report
- Report fixture source, parsed counts, cache/displayed-time evidence, unresolved live-only semantics, and isolation result.
