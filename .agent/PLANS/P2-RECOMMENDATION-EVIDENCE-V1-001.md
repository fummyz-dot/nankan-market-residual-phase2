# P2-RECOMMENDATION-EVIDENCE-V1-001

## Inputs

- Final immutable P8 live-shadow bundle bytes and existing `recommendation` block.
- Existing canonical live-ledger race identity and transaction helpers.
- Existing T15/fallback reference provenance embedded in the bundle.

## Outputs

- `recommendation_records` and `recommendation_tickets` ledger tables.
- One deterministic, validated evidence record per canonical race key.
- `race-shadow` output that reports committed or existing evidence before
  `ANALYSIS_READY` is rendered.
- Targeted unit/integration/smoke audit artifacts.

## Invariants and exclusions

- The writer validates but never recalculates WIN/WIDE policy output.
- Bundle bytes are finalized before evidence transaction; evidence stores their
  SHA-256 and the bundle file is not modified after that hash is computed.
- A repeated equivalent request is idempotent; a changed request for the same
  race fails closed.
- No `actual_bets`, result/outcome database, payout, settlement, P/L, model,
  feature, T15/fallback, or policy change is in scope.
- Engineering replay does not create operational recommendation evidence.

## State and transaction

1. P8 builds and atomically writes final bundle bytes.
2. Writer hashes final bytes, validates bundle recommendation/provenance, then
   begins `BEGIN IMMEDIATE` with foreign keys on.
3. Resolve/register the existing natural-key race parent; insert parent and
   ticket children; validate; commit.
4. Only committed/idempotent evidence is eligible for `ANALYSIS_READY`.
5. DB failure leaves a final bundle file but emits no `ANALYSIS_READY`; retry
   verifies the same bundle and retries the transaction.

## Acceptance tests

- BET/NO_BET, ten tickets, idempotency, different-payload conflict, invalid
  totals/inactive selections/canonical duplicate WIDE rejection.
- T15 and RECOVERY fallback provenance, DB failure/retry, legacy-result
  collector independence, actual-bet/result access isolation.
- Fresh-process top-level temp-DB smoke.
