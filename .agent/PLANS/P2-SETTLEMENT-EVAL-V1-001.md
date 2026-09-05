# P2-SETTLEMENT-EVAL-V1-001

## Inputs

- Existing official result raw/capture provenance and `official_payouts`.
- Immutable `P2_RECOMMENDATION_EVIDENCE_V1` records/tickets, plus legacy
  pre-post `FROZEN` Decisions only.
- Existing stored runner predictions and official result rows.

## Outputs

- Canonical WIN/WIDE payout semantics audit, settlement/evaluation ledger,
  `race-evaluate` CLI, and daily report artifact.

## Invariants and exclusions

- Post-race only; never imported by prediction/race-shadow paths.
- Actual bets, model/feature/WIDE/policy/fallback changes, strategy backfill,
  automatic correction, settlement of TRIO, and tuning are excluded.
- Evidence is primary; legacy frozen Decisions are fallback. Conflicting dual
  strategies fail closed. `NO_BET` and `NO_PRE_RACE_RECOMMENDATION` remain
  distinct.

## Transactions and failure handling

1. Resolve source strategy and final official result/payout data read-only.
2. Validate exact source hashes and payout completeness for required ticket
   types before opening a settlement transaction.
3. Insert immutable race settlement and ticket children with FK-on validation.
4. Equivalent source hashes are idempotent; any source hash change fails closed.
5. Generate the daily report only from committed/current settlement rows.

## Acceptance tests

- WIN/WIDE hit/miss/refund, multiticket/ten ticket, NO_BET, absent strategy,
  evidence/legacy/equal-dual/conflict-dual, legacy stake normalization,
  incomplete payout, source-change, idempotency, LL signs, reference splits,
  and zero actual-bet access.
- Fresh-process temporary-ledger smoke before official 2026-08-24 evaluation.
