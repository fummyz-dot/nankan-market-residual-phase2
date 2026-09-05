# P2 Nankan-specialized prospective collection contract V1

## Status and identity

`COLLECTION_CONTRACT_ID: P2_NANKAN_SPECIALIZED_COLLECTION_CONTRACT_V1`  
`COLLECTION_CONTRACT_SHA256: 1abe874932ee1ad373faccc0b83ac35828d3765c1f493d38846fc8e2554a8718`

This is a measurement-only continuation.  It does not reopen 031, train M1,
evaluate outcomes, choose features/thresholds, or emit a bet.

`COHORT_START_DATE: 2026-09-04`.  The supplied official schedule authority
states Ohi 1R at 14:50 JST and T15 at 14:35 JST.  This date is effective only
because the contract, implementation tests, synthetic collection-only dry run,
deterministic replay, and no-bet proof completed before 2026-09-04 13:35 JST.
If any of those had not completed, 2026-09-04 would be excluded irrevocably and
the start date would have been 2026-09-07.  The authority snapshot and its hash
are retained in the contract manifest.

## Collection-only execution

`./specialized-collect validate --input <official-authority-envelope.json>`
validates a no-write envelope.  `ingest` writes the append-only specialized
raw-authority ledger; `replay` verifies its deterministic day manifest; and
`status` emits only non-outcome collection metrics.  Every command emits
`ACTUAL_BUY=false`, `MANUAL_BUY_RECOMMENDED=false`, and
`no_bet_confirmation=true`.

The envelope is populated only from retained official raw evidence.  Raw
authority and the original day plan are immutable.  Schedule changes are
separate append-only revisions; no decision-time schedule is overwritten.
Derived feature definitions are deliberately outside this contract.

## Exact T15 authority

The original official/public schedule known at collection is retained with its
source, capture time, raw reference, and checksum.  `decision_time` is exactly
that time minus 15 minutes.  A WIN snapshot is valid only when
`D-60 seconds <= captured_at <= D`; both endpoints are inclusive.  Late and
stale raw captures remain evidence but cannot be promoted.  A valid WIN record
requires complete active roster, parseable positive odds for every active
runner, scratch status, source, capture time, and raw checksum.

WIN race-snapshot and runner-odds quality targets are respectively >=99% and
>=98%.  These are collection-quality gates, not outcome criteria.  WIDE/TRIO
may be retained only as `PASSIVE_FUTURE_AUTHORITY_ONLY_CAPTURED`; they cannot
generate a research candidate, policy, or status promotion.

## CURRENT and coverage

Official day-header authority is captured at T15 for `weather_raw`,
`track_surface_raw`, and `going_raw`, plus source reference, capture time, and
raw checksum.  A same-domain fallback is permitted only when primary is absent;
a conflict is `SOURCE_CONFLICT`.  Blank or `－` is stored exactly as
`SOURCE_NOT_PUBLISHED_AS_OF_T15`.  No result page may backfill T15 going/weather.

Each CURRENT item has raw value, deterministic normalized value when available,
source, capture time, status, and missing reason.  The runner-major fields are
bodyweight, bodyweight-change/status, current-jockey ID, and jockey-change
status.  Valid cells are `VALUE` or `STRUCTURAL_NA`; unexplained missing is
invalid.  The race-major fields are going and weather.

`RUNNER_MAJOR_COVERAGE = valid runner cells / (eligible runners * 4)`  
`RACE_MAJOR_COVERAGE = valid race cells / (eligible races * 2)`  
`CURRENT_MAJOR_COVERAGE = min(RUNNER_MAJOR_COVERAGE, RACE_MAJOR_COVERAGE)`

The required gate is `CURRENT_MAJOR_COVERAGE >= .95`, with all three metrics
reported separately and race fields never runner-weighted.

## Day plan, pace, and same-day evidence

The day plan contains every official race at its Nankan venue/date and is frozen
at least 60 minutes before its first T15.  `CANCELLED_PRE_T15` is recorded but
excluded from the denominator.  A cancellation/abandonment after T15 remains an
eligible collection race and can later be labelled `NO_VALID_OUTCOME`; it is not
silently removed.  A complete day has a valid collection disposition for every
non-pre-T15-cancelled race.  Official not-published-as-of states are valid
dispositions; collector/parser/source-conflict failures are not.

Pace authority retains only strict-as-of raw history needed to later freeze one
low-dimensional definition: race chronology, prior official passing-position
raw when present, field size, distance, venue, going, class, and raw references.
It does not compute style, pace-pressure, or any model feature.

P4 same-day result polling is isolated from P0/P1/P2.  Attempts are at +120,
+180, +240, +300, +420, and +600 seconds after scheduled post, at most six,
with <=8-second request timeout.  Any attempt inside an upcoming T15 protection
window `[D-90s, D+30s]` is `DEFERRED`; it never delays a market/current capture.
Result evidence retains attempts, `first_seen_official_at`, raw reference/hash,
result status, and passing-position authority when present.

Only `first_seen_official_at <= target.decision_time` may enter that target's
future same-day state.  Valid states are `NO_PRIOR_SAME_DAY_RACE`,
`PRIOR_RESULT_NOT_AVAILABLE_AS_OF_DECISION`, and `AVAILABLE_AS_OF_DECISION`.
`COLLECTOR_FAILURE`, `PARSER_FAILURE`, and `SOURCE_CONFLICT` are quality
failures tracked separately.  Future state remains limited to at most
`FRONT_BACK_STATE` and `DRAW_POSITION_PROXY_STATE`; gate/draw is never called
actual running path.

## Gates and cap

- First 20 COMPLETE race-days: instrumentation quality only; no M0-vs-M1 test.
- At 40 COMPLETE race-days: exact T15, CURRENT, pace, same-day, inventory,
  replay, and source semantics must be stable or
  `SPECIALIZED_DATA_COLLECTION_GIVEUP_TRIGGERED`.
- M1 design is not authorized before >=80 COMPLETE days, >=2,500 WIN 8–25
  runners, >=900 target-support races, four venues, quality gates, and replay.
- The later design freeze applies a 160-future-COMPLETE-day practicality gate.
- The absolute cap is 240 COMPLETE race-days or 12 calendar months from cohort
  start, whichever occurs first.

No outcome-guided pace/same-day definition, venue selection, band change,
threshold change, ROI work, WIDE reopening, or TRIO strategy is allowed.
