# Codex Job Plan

## Job metadata
- Job ID: P2-A02A
- Title: Prospective Input & Snapshot Foundation
- Status: COMPLETED
- Owner: Codex

## Objective
Build an auditable, source-agnostic prospective capture foundation without inferring any live-source parser semantics.

## Allowed inputs
- Phase 2 policy, A01/A01R reports, local synthetic fixtures, and user-submitted URLs only when explicitly invoked later.

## Read-only inputs
- `reference/v1/` and `/home/nabe/projects/nkDb-pro/`.

## Allowed modifications
- `src/ingestion/`, `src/validation/`, `src/audit/`, `tests/`, `db/`, `data/raw/`, `data/manifests/`, `audit/data/p2_a02a/`, `reports/development/`, `.agent/`, and active Phase 2 policy/contracts.

## Forbidden actions
- No V1 writes; no model, performance, ROI, or feature-effectiveness work; no live-source selector/API inference; no decision-time freeze; no primary promotion of Keibabook.

## Tasks
1. Define the prospective SQLite schema, raw archive, URL capture interface, and source manifest.
2. Add whitelist-only current-info and external Keibabook quarantine paths.
3. Add bounded process-supervision, heartbeat, progress, checkpoint, marker, and orphan-audit primitives.
4. Document contracts, implement synthetic tests, and emit deterministic audit artifacts/manifests.

## Required artifacts
- All files specified under `audit/data/p2_a02a/` and the P2-A02A development report.

## Tests / acceptance criteria
- SQLite round-trip, raw hash/manifest, sanitizer/quarantine, prohibited-field, timestamp, and supervision tests pass with local synthetic data.
- No source-specific parser is registered without a supplied live sample.
- All timestamps are timezone-aware and `PRIMARY_FROZEN` is rejected.

## Leakage and temporal checks
- `P2_CURRENT` uses a positive allow-list only.
- Post-primary snapshots are diagnostic-only and not primary-candidate eligible.
- Keibabook records are metadata-separated as `P2X_O` or `P2X_S`.

## Run manifest requirements
- `vcs_mode: none`, `git_commit: null`, SHA-256 code/input/config/output manifests, environment, null random seed, commands, and artifacts.

## Completion report
State the foundation readiness, live-source unknowns, process outcomes, and confirmation that no research semantics changed.
