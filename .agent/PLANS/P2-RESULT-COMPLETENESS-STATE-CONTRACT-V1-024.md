# P2-RESULT-COMPLETENESS-STATE-CONTRACT-V1-024

## Inputs

- Existing official result collector/raw archive and final-result ledger.
- Existing strict `parse_history_result_fields` and live-history promotion path.
- Frozen WIN/WIDE settlement, Actual Accounting, and race-day exit contracts.

## Outputs

- Immutable source-SHA-bound result-completeness assessments with independent
  source, history-readiness, and WIN/WIDE/TRIO payout-readiness axes.
- Compact race-day POST rendering/events for waiting, partial, and readiness
  states, without changing settlement or history promotion.
- Focused synthetic regression coverage and the V1 contract document.

## Invariants / exclusions

- Final result tables remain final-only; incomplete children are never written.
- Target-day history is never promoted during POST; next-prepare remains the
  only promotion path.
- `RESULT_OFFICIAL_FINAL`, settlement formulas, FS04/model/policy, and
  research membership are unchanged.
- Different raw content after accepted finality fails closed.

## State and failure handling

1. No result raw is an operational `RESULT_WAITING`, with no fabricated SHA.
2. Exact identified raw that is not final records one immutable partial
   assessment keyed by `(race_key, raw_sha256)`.
3. A strict history parser pass is readiness only, not promotion.
4. Per-ticket payout readiness is assessed independently; settlement still
   waits for the existing final source predicate.
5. Same source SHA is idempotent; a new SHA before finality creates a new row;
   a new SHA after finality is an immutable source conflict.

## Acceptance

- Source/history/payout axis tests, source progression/idempotency, race-day
  POST visibility and exit tests, plus targeted collector/settlement/history
  regressions and `compileall`, all in `.venv-p2-model`.
