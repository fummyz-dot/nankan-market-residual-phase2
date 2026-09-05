# Codex Job Plan

## Job metadata
- Job ID: P2-A02B2-PREP
- Title: Live Freshness Probe Implementation
- Status: COMPLETED
- Owner: Codex

## Objective
Implement one foreground, one-race live-freshness observation runner using retained fixture bytes and mocked clocks only.

## Allowed inputs
- A02B-1 adapter, A02A storage/sanitizer, retained historical fixture raw bytes, and local synthetic/mocked HTTP data.

## Read-only inputs
- `reference/v1/`, V1 original, and historical fixture raw bytes after their initial retention.

## Allowed modifications
- `src/operations/`, `tests/`, `outputs/live_freshness/`, `audit/data/p2_a02b2_prep/`, `reports/development/`, `data/manifests/`, and active state/contract docs.

## Forbidden actions
- No live URL access; no background/child process; no model training/inference/evaluation/ROI/result retrieval; no T-15 freeze; no V1 writes.

## Tasks
1. Build a clock-injected, foreground mark scheduler with a bounded fetch interface, atomic checkpoints, and terminal markers.
2. Reuse A02B-1 DOM discovery/parser and A02A quarantine/storage contracts for every capture mark.
3. Emit a separate machine-readable freshness JSON and adjacent-capture comparisons.
4. Verify scheduling, failures, resume/no-backfill, marker cleanup, isolation, and JSON schema with fixture/mocked tests.

## Required artifacts
- All requested files under `audit/data/p2_a02b2_prep/` and the P2-A02B2-PREP report.

## Tests / acceptance criteria
- T20/T15/T10/T5 mocked flow, too-early/start-late, monotonic wait, bounded error continuation, checkpoint atomicity, marker cleanup, no background process, quarantine, candidate-not-frozen, and final JSON schema all pass.

## Leakage and temporal checks
- Missed marks are recorded, never backfilled. T-15 uses only `PRIMARY_CANDIDATE` plus `LIVE_FRESHNESS_TEST`; no fixture/live source is promoted to a model input.

## Process supervision
- A single foreground process uses no child/background processes. It writes its own atomic run markers and checkpoints.

## Run manifest requirements
- Gitless SHA-256 code/input/config/output provenance, null random seed, command record, and environment.

## Completion report
- State readiness for a user-supplied live URL, all remaining freshness unknowns, and the no-live-access result for this implementation job.
