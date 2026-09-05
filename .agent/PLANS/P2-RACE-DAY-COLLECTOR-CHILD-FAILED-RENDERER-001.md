# P2-RACE-DAY-COLLECTOR-CHILD-FAILED-RENDERER-001

## Job metadata

- Job ID: P2-RACE-DAY-COLLECTOR-CHILD-FAILED-RENDERER-001
- Title: Render managed collector child failure safely in `race-day`
- Status: COMPLETE
- Owner: Codex

## Objective

Add the known `RACE_DAY_COLLECTOR_CHILD_FAILED` renderer branch so the CLI
reports the failure without requiring the ready-form payload contract.

## Allowed inputs

- `src/operations/race_day.py`
- `tests/unit/test_p2_race_day_v1.py`
- `docs/P2_RACE_DAY_V1_OPERATIONS.md`
- `docs/CODEX_WORKFLOW.md`
- this plan

## Read-only inputs

- `reference/v1/`
- Existing live-development evidence, including `outputs/live_development/2026-08-31/`

## Allowed modifications

- `src/operations/race_day.py`
- `tests/unit/test_p2_race_day_v1.py`
- `.agent/PLANS/P2-RACE-DAY-COLLECTOR-CHILD-FAILED-RENDERER-001.md`
- `audit/data/p2_race_day_collector_child_failed_renderer_001/run_manifest.json`

## Forbidden actions

- No change to collector lifecycle, failure detection, status, resume, plan,
  capture, research, result, settlement, or database semantics.
- No database writes, web access, dependency additions, or changes to 2026-08-31 evidence.
- No writes to `reference/v1/`.

## Tasks

1. Inspect the existing compact renderer and renderer-adjacent tests.
2. Add one status-specific branch before the ready-form accesses.
3. Add direct renderer regression coverage for the failed-child payload and
   regression checks for ready, complete, and stopped outputs.
4. Run the focused renderer tests and record a gitless provenance manifest.

## Required artifacts

- Focused regression test in `tests/unit/test_p2_race_day_v1.py`
- `audit/data/p2_race_day_collector_child_failed_renderer_001/run_manifest.json`

## Tests / acceptance criteria

- Minimal failed-child payload renders with no exception and includes status,
  date, venue, and the prescribed action.
- Missing `targets`, `last_target`, and `keibabook` are accepted for that status.
- Existing ready, complete, and stopped renderer output remains covered.

## Leakage and temporal checks

- This is a pure formatter change: no model feature, market, outcome, payout,
  decision-time, eligibility, or evidence data is read or changed.

## Failure and state handling

- The explicit failure payload is terminal display-only data.  It does not
  alter collector state or attempt retry/resume.
- Optional detail is shown only if an existing `reason`, `error`, or
  `returncode` field is present; absent fields are not inferred.

## Process supervision

No worker is created or supervised by this renderer-only change.

## Run manifest requirements

- `vcs_mode: none`; `git_commit: null`; non-stochastic seed `null`.
- Record SHA-256 manifests for code, inputs/configuration, output artifact,
  platform/library versions, and executed commands.

## Completion report

Report the exact branch, tests, and confirmation that scientific semantics,
database writes, and web accesses remain zero.

## Completion

- Added only the explicit `RACE_DAY_COLLECTOR_CHILD_FAILED` branch before
  ready-form fields are accessed.
- Added direct renderer regression coverage and passed the focused renderer
  tests, complete `test_p2_race_day_v1` suite, and compilation check.
- No live evidence, persistent database, collector behavior, or scientific
  semantics was accessed or changed.
