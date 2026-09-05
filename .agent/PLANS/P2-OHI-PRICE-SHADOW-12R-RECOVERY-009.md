# P2-OHI-PRICE-SHADOW-12R-RECOVERY-009

## Inputs

- Immutable 2026-09-01 Ohi 12R T15 Price Shadow selection.
- Exact stored pre-race T10/T05 MARKET WIDE captures from the market DB.
- Existing incomplete 12R trajectory and terminal first-three Price Support state.

## Authorized operation

1. Read-only verify exactly one natural-key T05 WIDE capture, its pre-post timestamp, complete roster/book, and selected pair.
2. Record all protected artifact hashes and pre-state.
3. Invoke the existing Price Shadow `run()` once in a fresh production-venv process, using the existing T15 artifact and saved market DB only.
4. Verify monotonic trajectory promotion, terminal Price Support invariance, and protected Experimental/Main/Funabashi artifacts.
5. Write only the authorized recovery trajectory/state effects and audit artifacts.

## Invariants

- No pair reselection, result/payout/settlement access, production DB write, Experimental invocation, purchase action, or source change.
- Price Support remains terminal and its first three keys remain 7R/10R/11R.
- Existing T15 and Experimental intent remain byte-identical.

## Acceptance

- 12R trajectory moves from `TRAJECTORY_INCOMPLETE` to `VALID_TRAJECTORY` through `_commit_trajectory()`.
- T10/T05 provenance is exact and pre-race.
- Targeted actual OHI Price Shadow tests and production-venv compileall pass.
