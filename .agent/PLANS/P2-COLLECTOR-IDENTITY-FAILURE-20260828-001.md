# P2-COLLECTOR-IDENTITY-FAILURE-20260828-001

## Job metadata

- Job ID: P2-COLLECTOR-IDENTITY-FAILURE-20260828-001
- Title: Determine and repair the 2026-08-28 Funabashi 12R collector identity failure
- Status: BLOCKED_SOURCE_PROVENANCE_INSUFFICIENT
- Owner: Codex

## Objective

Determine the saved-source root cause of the 12R T15 identity failure and, only
if an existing unambiguous semantic path supports it, make the smallest collector
repair that prevents the same pre-race capture miss.

## Allowed inputs

- `src/operations/prospective_day_collector.py` and its existing identity/parser helpers
- existing relevant unit/integration tests and saved raw fixtures
- 2026-08-28 collector logs, checkpoints, event ledger, market DB records, and
  pre-race source-capture provenance

## Read-only inputs

- `reference/v1/`
- 2026-08-28 Recommendation Evidence, analysis bundles, and WIN/WIDE/CURRENT
  research evidence

## Allowed modifications

- narrow collector/parser code already responsible for the confirmed failure
- its existing test file(s)
- this plan and a Phase 2 audit run manifest

## Forbidden actions

- no writes to V1 reference/original repo;
- no fuzzy or name-only identity fallback;
- no new module, class, table, CLI, configuration key, dependency, generic
  identity framework, policy/model/FS04/DEV-LIVE-V1 change, or post-hoc T15;
- no silent roster drop or ambiguous identity resolution.

## Tasks

1. Correlate exact 12R T15 raw/provenance, checkpoint, event, exception, and
   current/market/combined commit path. Record the entity-level facts or the
   exact missing provenance that blocks determination.
2. Classify only from saved official source evidence. Search for an existing
   production semantic (`UNKNOWN`, `UNRESOLVED`, withdrawal, or exact
   crosswalk) that safely covers the confirmed cause.
3. If a reuse path exists, apply its minimum connection and add regressions for
   the 12R fixture, normal T15, withdrawal, unseen trainer, ambiguity, active
   roster, shared WIN/WIDE capture-set, and recommendation immutability.
4. Run a fresh-process saved-fixture smoke against temporary/copied DB state,
   with result access asserted zero. Compare all immutable 2026-08-28 Evidence
   hashes before/after. (Not run: the only exact failing response was not
   retained, so an acceptance rule for the metadata difference would be
   speculative.)

## Required artifacts

- `audit/data/p2_collector_identity_failure_20260828_001/` root-cause report,
  run manifest, and immutable-evidence SHA-256 comparison

## Tests / acceptance criteria

- Exact failing entity and stage are source-proven, or the job is BLOCKED.
- Any repair preserves ambiguous-identity blocking and active-roster integrity.
- T15 fixture captures and commits successfully only with legal existing
  semantics; WIN/WIDE use the identical accepted capture set.
- Result access is zero in fresh-process smoke; all frozen Evidence hashes match.

## Leakage and temporal checks

- Saved pre-race raw data only; no result/payout/final-odds access for the
  collector test path.
- 12R remains `PRE_RACE_FALLBACK/T20`; no post-hoc capture, promotion, or
  recommendation rewrite.

## Process supervision

- All fixture work is foreground, synchronous, bounded, and uses temporary
  state. No new worker/supervisor is introduced.

## Run manifest requirements

- `vcs_mode: none`
- `git_commit: null`
- workspace root, SHA-256 code/input/config manifests, Python/platform/library
  versions, `seed: null`, commands, and output hashes

## Completion report

Report the source-proven root cause/classification, entity-level facts, exact
failure stage, minimal repair or BLOCKED reason, tests/smoke, immutable hash
comparison, and next-live readiness.

## Blocker

The T15 response was rejected before `archive_bytes`, card parsing, runner
identity resolution, market fetch, or combined-snapshot commit.  The saved
checkpoint and event ledger retain only the generic exception, not the two
identity dictionaries or the response bytes.  `identity != task.identity`
compares material scheduled-post/card metadata as well as the canonical race
key.  `P2_RACE_DAY_V1_OPERATIONS.md` requires a material card-metadata conflict
to stop (`DAY_PLAN_CONFLICT`); accepting any unrecorded differing field would
therefore weaken the frozen-plan/time contract.  No source-proven production
semantic permits that change.  See the audit root-cause report.
