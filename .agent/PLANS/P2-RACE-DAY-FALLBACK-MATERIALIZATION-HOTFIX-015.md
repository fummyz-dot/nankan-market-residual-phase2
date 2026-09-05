# P2-RACE-DAY-FALLBACK-MATERIALIZATION-HOTFIX-015

## Objective

Correct the `race_shadow.run()` post-RECOVERY validation clock so a valid
synchronous RECOVERY capture is never revalidated against the caller's stale
pre-RECOVERY tick time.

## Inputs

- `src/operations/race_shadow.py`
- Existing shared pre-race reference policy and focused race-shadow/race-day
  tests.

## Invariants

- Preserve the frozen T15 window, fallback age and minimum-post-time bounds,
  scientific-sample classification, model, FS04, and bet-policy semantics.
- Retain negative-age rejection for genuinely inconsistent evidence.
- No scheduler, collector, database, result, payout, settlement, or model
  changes.

## State transition

1. Select with the tick timestamp and, if needed, execute bounded RECOVERY.
2. On successful synchronous RECOVERY, refresh the validation timestamp.
3. Materialize the selected standard/fallback reference with that refreshed
   timestamp; ordinary fail-closed validation remains unchanged.

## Acceptance

- Stale-now RECOVERY succeeds using a post-RECOVERY validation time.
- Exact 900-second / 120-second boundaries and genuine negative-age failures
  retain their established semantics.
- Relevant race-shadow, race-day, fallback and recommendation-evidence tests
  pass with the production project venv.
