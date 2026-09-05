# P2-TRIO-PROSPECTIVE-SHADOW-V0-001 — TRIO prospective shadow V0

## Job metadata

- Job ID: P2-TRIO-PROSPECTIVE-SHADOW-V0-001
- Title: TRIO Prospective Shadow V0
- Status: COMPLETE
- Owner: Codex

## Objective

Add a research-only, prospective TRIO evidence and post-race CE path using only
the retained exact pre-race capture set and frozen WIDE joint/DEV-LIVE-V1
artifacts, without affecting the Main recommendation or betting.

## Allowed inputs

- `src/operations/prospective_day_collector.py` and existing market snapshot/raw
  archive primitives;
- `src/ingestion/adapters/nankan_official.py` official TRIO parser and explicit
  odds-link resolver;
- `src/operations/wide_research_shadow.py` frozen J0/J1 solver primitives and
  bundle verifier;
- `src/operations/wide_ops_v0.py` exact PL Top3 construction pattern;
- committed Recommendation Evidence and existing official result tables.

## Read-only inputs

- `reference/v1/`;
- `models/development/wide_prospective_v1/` and the frozen DEV-LIVE-V1 model;
- existing Recommendation, WIN, WIDE, CURRENT, and Market Trajectory evidence;
- all existing 2026-08-28 evidence and raw captures.

## Allowed modifications

- narrow TRIO capture persistence in `src/operations/prospective_day_collector.py`;
- TRIO materialization/evidence/evaluation/race-day sibling paths only where
  existing WIDE infrastructure cannot represent unordered TRIO evidence;
- `src/operations/live_development_store.py` only if a distinct immutable TRIO
  evidence/evaluation table is required;
- focused unit/integration tests, the frozen V0 bundle, and this job's audit
  manifest under Phase 2 namespaces.

## Invariants and exclusions

- Main Recommendation, DEV-LIVE-V1, FS04, Policy, WIN/WIDE/CURRENT/Trajectory,
  settlement semantics, and all existing evidence are immutable.
- Pre-race code has `result_db_accessed=0`; prediction is after Main Evidence,
  before post only, and is nonblocking.
- TM0 uses exact complete TRIO odds only; TJ0/TJ1 aggregate the existing
  unordered Top3-set joint; TPL aggregates the exact existing PL Top3 order.
- No model/beta/gamma/odds-band/GER/stake/feature search; bets and stakes are
  always disabled/zero.
- Incomplete/duplicate/invalid TRIO odds, solver failure, ambiguous outcome,
  post-reference withdrawal, and immutable-payload conflict fail closed without
  overwriting evidence.
- 2026-08-28 engineering replay is confirmation-excluded and is never promoted
  to a synthetic T15 scientific sample.

## State and transaction boundaries

1. Capture an explicitly linked TRIO source at an existing T-mark and persist
   raw provenance plus every canonical ticket atomically with CURRENT/WIN/WIDE.
2. After Main Evidence, verify frozen artifacts, resolve the exact retained
   capture set, build all `C(n,3)` probabilities, write immutable audit bytes,
   and insert one append-only research record transactionally.
3. Retry identical inputs as no-op; reject a different immutable payload. At or
   after post, create a missed marker only; never backfill a pre-race sample.
4. Only after the race-day pre-race barrier, derive a safe official unordered
   TRIO outcome, write one immutable evaluation, and aggregate Primary and
   fallback scopes separately.

## Required artifacts

- `models/development/trio_prospective_v0/` frozen manifest with the fixed
  arms, hashes, beta, milestones, delta and confirmation start;
- TRIO pre-race/evaluation JSON evidence and cumulative report under the
  established `outputs/live_development/` convention;
- task audit/run manifest with source/code/config/output hashes and zero
  pre-race result access.

## Tests / acceptance criteria

- complete 5/11/13/14-runner TRIO, missing/duplicate/invalid ticket, six-order
  aggregation, all-arm normalization, Primary/fallback/drift/withdrawal,
  idempotent/conflict/restart, dead-heat/ambiguous outcome, and Main/WIN/WIDE/
  CURRENT/Trajectory isolation;
- fresh-process temp-DB smoke for complete T15, fallback, incomplete source,
  restart, and post-race evaluation, including zero result access before post.

## Run manifest requirements

- `vcs_mode: none`; `git_commit: null`; workspace/timestamp; code/input/config
  SHA-256 manifests; platform/library versions; `random_seed: null`; commands
  and output hashes.

## Completion report

Report source/capture path, frozen hashes, primary/fallback and outcome
semantics, test/smoke/runtime evidence, unchanged protected hashes, exact
changed files, and any blocker without inferred semantics.
