# P2-LIVE-HISTORY-INCOMPLETE-SAVED-RESULT-FALLBACK-001

## Objective

Make `live_history_update.ingest_race()` reuse a retained settlement result raw
only when the existing strict `official.parse_history_result_fields()` accepts
it for the current card-derived identity.  An ineligible saved raw must remain
immutable and trigger exactly one fresh approved result-URL fetch.

## Inputs

- `src/operations/live_history_update.py`
- existing immutable saved 2026-08-28 Funabashi 12R partial result raw
- `/tmp/p2_20260828_funabashi12_full_result.html` as byte-identical offline
  final-result fixture source
- existing official result/card parsers and live-history normalization path

## Outputs

- Narrow `ingest_race()` saved-result eligibility branch and outcome provenance.
- Existing relevant tests plus an offline fresh-process smoke artifact.
- Run manifest under the Phase 2 audit namespace.

## Invariants / exclusions

- Do not alter the result parser, field-size/starter/roster semantics, status
  vocabulary, settlement `_saved_result_page()`, saved settlement raws, DB
  schema, modules, dependencies, CLI, or research artifacts.
- Catch only `ValueError` from parsing a saved raw for history-cache
  eligibility.  Fresh result parsing remains fail-closed.
- The committed `OFFICIAL_RESULT` capture must be the selected fresh page when
  fallback succeeds.
- Transaction rollback leaves no race/runner/capture promotion on failure.

## Acceptance tests

1. Real 8/28 partial saved raw (3 runners) is rejected; byte-identical final
   fixture (13 ordered runners) is fetched once, parsed, and committed.
2. Complete saved raw is reused without a fresh result fetch.
3. Rejected saved raw plus invalid fresh raw fails with zero delta promotion.
4. No saved raw retains ordinary fresh fetch/parse/commit behavior.
5. Replaying a successful fallback is `IDEMPOTENT_NOOP` with no conflicting
   children; normalized refresh succeeds in a new Python process and reports
   `result_db_accessed: 0`.

## Failure handling / idempotency

- Saved-parser `ValueError` is retained solely as
  `saved_result_raw_reuse_rejected_reason` and does not weaken validation.
- Other saved-parser exceptions propagate.
- HTTP non-success and fresh-parser errors propagate before DB initialization
  and transaction promotion; existing rollback protects later failures.

## Completion

- Status: `P2_LIVE_HISTORY_INCOMPLETE_SAVED_RESULT_FALLBACK_READY`.
- The real 8/28 saved raw was verified as 3 rows and rejected by the unchanged
  history parser; the byte-identical final fixture was verified as 13 rows in
  order `12,7,13,3,11,5,10,9,2,6,8,1,4` and committed as the sole result raw.
- Unit/regression tests, compile check, and fresh-process copied-DB smoke are
  recorded in the accompanying Phase 2 audit artifacts.
