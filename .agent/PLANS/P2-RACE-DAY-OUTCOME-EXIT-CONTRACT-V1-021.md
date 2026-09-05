# P2 Race-Day Outcome Exit Contract V1

## Objective

Implement only the race-day CLI classification boundary: one explicit 0/10/20 classifier and one final machine-readable outcome block.

## Invariants

- Preserve all existing scientific scheduling, Main/Research behavior, collector capture behavior, accounting formulas, and immutable evidence semantics.
- `PENDING_CONFIRMATION` remains a scientifically complete, exit-0 human-action state.
- Keep argparse (2/help 0) and unhandled-Python-exception behavior unchanged.
- Use only synthetic/unit fixtures for validation; no production result/payout access.

## State transitions

1. Convert existing terminal run values and explicit `RaceDayError` reasons into one classified CLI outcome.
2. Retain race-local blocked observations until final classification, without changing the scheduler.
3. Inspect existing collector completion summary after nonzero child exit to distinguish retained capture failure from missing/contradictory supervision evidence.
4. Render exactly one final `RACE_DAY_OUTCOME` block and exit through `SystemExit(classified_code)`.

## Acceptance tests

- Direct `main()` matrix: 0, 10, 20, argparse 2.
- Collector 0/nonzero/`COMPLETE_WITH_FAILURES`/contradiction matrix.
- Actual Accounting pending/complete/wait/error matrix.
- Race-local block severity and renderer single-block/idempotent resume regression.

## Completion

- Implemented the central `RaceDayExitClass` classifier and one final outcome block for every normal CLI termination.
- Added collector terminal-summary validation that distinguishes retained `COMPLETE_WITH_FAILURES`, missing child evidence, and contradictory evidence.
- Passed the targeted 67-test synthetic/unit suite and production-venv `compileall` without result/payout access.
