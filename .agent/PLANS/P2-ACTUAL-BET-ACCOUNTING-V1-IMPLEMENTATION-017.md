# P2-ACTUAL-BET-ACCOUNTING-V1-IMPLEMENTATION-017

## Objective

Implement immutable Main and Experimental actual-purchase action evidence,
file-derived WIN/WIDE actual settlement, daily/cumulative cash reports, and
the existing race-day POST integration point.

## Inputs

- Immutable `P2_RECOMMENDATION_EVIDENCE_V1` records/tickets.
- Existing approved WIDE Experimental intents and confirmation artifacts.
- Existing official-final result/payout ledger and deterministic ticket core.

## Invariants

- Do not write, migrate, or read for settlement `actual_bets`.
- Actual cash accounting is separate from recommended-strategy settlement.
- Only authorized Main Recommendation or WIDE Experimental sources are valid.
- Confirmation is pre-post, explicit, hash-bound, append-only, and terminal.
- No production 2026-09-01 actual evidence, result/payout value inspection,
  model/policy/feature change, or automatic purchase.

## State transitions

1. A Main ticket or Experimental intent receives exactly one explicit final
   `PURCHASED` or `NOT_PURCHASED` action artifact.
2. Only purchased actions load into race-level actual settlement.
3. Daily accounting derives confirmation completeness and settlement status.
4. Cumulative accounting rebuilds from daily reports and preserves 2026-09-01
   as an accounting coverage gap until separately authorized import.

## Acceptance

- Synthetic isolated fixtures cover Main/WIDE confirmation, loader,
  WIN/WIDE/refund settlement, daily/cumulative formulas, and race-day resume.
- Existing purchase-confirmation, settlement, race-day, and recommendation
  evidence regressions remain green.
