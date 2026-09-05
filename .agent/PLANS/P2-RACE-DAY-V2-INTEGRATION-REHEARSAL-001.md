# P2-RACE-DAY-V2-INTEGRATION-REHEARSAL-001 — integration rehearsal

## Job metadata

- Status: COMPLETE_WITH_NONBLOCKING_WARNINGS
- Mode: audit/rehearsal only; production code, schemas, model artifacts, and
  production databases are out of scope.

## Inputs and boundaries

- Inputs: saved 2026-08-24 Funabashi pre-race fixtures, frozen V2 policy and
  research bundle, and temporary/copied SQLite databases only.
- Scenarios: normal T15, T-9 fallback/restart, existing evidence resume,
  research-unavailable isolation, 14-runner research, pre-race leakage,
  post-race settlement/evaluation, non-target 12R, V2 wide-only edge,
  V1 compatibility, raw display precision, Ctrl-C/resume, and integrity.
- Exclusions: production DB writes, new model/policy semantics, actual bets,
  source-network access, historical V2 backfill, and code changes unless an
  immediate hard operational failure is reproduced.

## Acceptance evidence

1. Use fresh Python processes and one-command-equivalent race-day orchestration
   with temporary state.
2. Confirm policy V2 Main isolation, recommendation/research idempotency, and
   no current-day result access before `PRE_RACE_CLOSED`.
3. Confirm only manifest targets are waited for and resume/lock/integrity
   behavior is clean.
4. Write the required audit files under
   `audit/data/p2_race_day_integration_rehearsal_20260826/`.

## Outcome

- Main V2 saved-fixture T15, fallback/restart, evidence resume, research
  isolation, post-race persistence/evaluation, non-target final race, and
  database integrity checks passed using fresh processes and temporary state.
- No production code or production DB was changed.
- Nonblocking findings: saved actual WIDE raw coverage is available for 11
  runners only (not 12/14); missing displayed precision currently reports
  `RESEARCH_WIDE_INVALID` rather than the requested `UNAVAILABLE` UX. Both
  are recorded in the rehearsal artifact; no semantic change was made.
