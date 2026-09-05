# P2 Actual Bet Retroactive Import 2026-09-01

## Objective

Import exactly two user-confirmed Main WIN purchases and one OHI Experimental WIDE non-purchase as immutable actual-purchase evidence, then rebuild only the Actual Accounting settlement and reports.

## Inputs and invariants

- Main authority: committed Recommendation Evidence ticket identified by exact `recommendation_id + ticket_index`.
- WIDE authority: exact OHI intent and raw SHA.
- Facts: 11R WIN #1 PURCHASED 100; 12R WIN #9 PURCHASED 100; 12R WIDE #7-#12 NOT_PURCHASED.
- Both WIN `placed_at` and `execution_odds` remain null. Import timestamp is not historical purchase time.
- Normal live deadline guards and ordinary CLIs remain unchanged.
- No legacy `actual_bets`, recommendation, official source, policy/model/feature, or research/Price Support mutation.

## State transitions

1. Resolve all three exact source records read-only; block on non-unique/mismatched linkage.
2. Use a bounded administrative importer with `RETROACTIVE_USER_CONFIRMED`; create only the listed actions atomically and idempotently.
3. Reuse Actual settlement and daily/cumulative rebuild paths.
4. Verify the importer rerun is a no-op, cash tickets number two, and unrelated artifacts are unchanged.

## Acceptance

- Three actions are SHA-bound and immutable; two WIN have null historical time/odds.
- Daily report is COMPLETE from official source data; cumulative removes the 2026-09-01 coverage gap.
- Normal live commands retain post-deadline rejection.
- Production DB writes and legacy actual_bets writes are zero.
