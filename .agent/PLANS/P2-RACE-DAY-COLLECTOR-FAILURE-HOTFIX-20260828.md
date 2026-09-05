# P2-RACE-DAY-COLLECTOR-FAILURE-HOTFIX-20260828

## Job metadata

- Job ID: P2-RACE-DAY-COLLECTOR-FAILURE-HOTFIX-20260828
- Title: Preserve managed collector child failure detail and resume post-race
- Status: COMPLETE
- Owner: Codex

## Objective

Fix the duplicate `reason` keyword failure in managed-collector failure reporting
without changing any pre-race decision, evidence, research, collector, model, or
policy semantics.

## Why this job exists

The orchestrator failed while reporting an already-terminated managed collector
child, obscuring the child's recorded terminal reason and preventing normal
post-race resume.

## Allowed inputs

- `src/operations/race_day.py`
- `tests/unit/test_p2_race_day_v1.py`
- `outputs/live_development/2026-08-28/船橋/` child logs, terminal record,
  day events, and immutable manifest
- `outputs/prospective_collection/2026-08-28/` collector run records

## Read-only inputs

- `reference/v1/`
- 2026-08-28 Recommendation Evidence, analysis bundles, and WIN/WIDE/CURRENT
  research evidence

## Allowed modifications

- `src/operations/race_day.py`
- `tests/unit/test_p2_race_day_v1.py`
- this plan and a Phase 2 hotfix run manifest/audit artifact

## Forbidden actions

- no writes to V1 reference/original repo;
- no pre-race evidence, research evidence, analysis bundle, model, policy, or
  collector architecture change;
- no post-hoc T15 generation;
- no new module, class, table, CLI, configuration key, or dependency.

## Tasks

1. Inspect the managed collector's terminal record, logs, and event schema to
   establish its actual exit/status/reason and the source of `detail["reason"]`.
2. Change only the failure event argument mapping so its top-level failure
   reason remains canonical and the child reason is retained under the existing
   event-detail field name, if present.
3. Add focused regression tests for normal child, child failure detail,
   `DAY_BLOCKED` emission, evidence-preserving restart, and post-race resume.
4. Run the focused suite and a bounded real-day post-race resume check; record
   before/after SHA-256 manifests for immutable 2026-08-28 evidence.

## Required artifacts

- `audit/data/p2_race_day_collector_failure_hotfix_20260828/` run manifest and
  immutable-evidence SHA-256 comparison

## Tests / acceptance criteria

- normal managed child is accepted;
- failed child emits `DAY_BLOCKED` without `TypeError`;
- top-level `reason` is `RACE_DAY_COLLECTOR_CHILD_FAILED` and underlying child
  reason is retained;
- restart recognizes committed evidence without invoking pre-race generation;
- post-race resume is reached without mutating pre-race evidence;
- focused unit suite passes and immutable evidence hashes match exactly.

## Leakage and temporal checks

- No result/payout access occurs until the existing pre-race barrier has closed.
- Resume does not generate an after-the-fact T15 capture or modify a
  `PRE_RACE_FALLBACK` provenance record.

## Process supervision

- Existing `ManagedCollector` remains the sole supervisor/child mechanism.
- The hotfix only preserves its terminal failure reason in the day event.
- Real-day closeout reads the existing child exit/status/stdout/stderr and
  records the existing orphan-process audit result; it creates no worker.

## Run manifest requirements

- `vcs_mode: none`
- `git_commit: null`
- workspace root, creation timestamp, SHA-256 code/input/config manifests,
  Python/platform/library versions, `seed: null`, commands, artifacts, and
  output SHA-256 values.

## Completion report

Report the collector root cause and its relation (or non-relation) to the 12R
T15 miss, the minimal event-field change, tests, immutable hash comparison,
and safe post-race resume command.
