# P2 Actual Bet Retroactive Settlement 2026-09-01

## Objective

Collect only the missing existing-contract official result/payout evidence for Ohi 11R/12R, then settle the already immutable Actual Purchase actions and rebuild Actual daily/cumulative accounting.

## Invariants

- Official source priority: local approved raw, then existing official collector only.
- Exact natural/P2 race identities; no manually entered result, payout, or odds.
- Existing Recommendation, Actual Purchase, T15, intent, Price Support, model and policy evidence are immutable.
- Monetary scope: 11R WIN #1 and 12R WIN #9 only. 12R WIDE #7-#12 remains NOT_PURCHASED.
- No legacy actual_bets write; no research/performance evaluation.

## State transitions

1. Locate or collect final official raw through the existing collector/parser.
2. Require normal `RESULT_OFFICIAL_FINAL` reconciliation for both races.
3. Reuse Actual Accounting settlement/daily/cumulative rebuild; compare to the pre-registered cash expectation.
4. Rerun and verify collector/settlement/report idempotency and unrelated evidence hashes.

## Completion

- Existing approved official collector fetched and immutably archived the missing 11R/12R raw; both ledger rows reached `RESULT_OFFICIAL_FINAL`.
- The established Actual Accounting path settled exactly the two purchased WIN tickets, kept the 12R WIDE action non-monetary, and produced `COMPLETE` daily/cumulative reports.
- A second collector run returned `IDEMPOTENT_NOOP` for both targets; a second bounded accounting run preserved the two-ticket 200-yen universe.
