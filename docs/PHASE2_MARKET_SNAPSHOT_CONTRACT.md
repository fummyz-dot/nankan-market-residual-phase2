# Phase 2 Market Snapshot Contract

`db/market_snapshot.sqlite.market_snapshots` is a Phase 2 v2 schema, separate from V1 `odds_snapshots`. Every record links to a race registry entry, raw capture, exact response SHA-256, capture timestamps, odds fields, race/scratch state, and quality/availability statuses.

Allowed roles are `INITIAL`, `PRIMARY_CANDIDATE`, `SECONDARY`, `EXECUTION_REFERENCE`, and `POST_PRIMARY_DIAGNOSTIC`. `PRIMARY_FROZEN` is prohibited. T-15 is recorded only as `T-15_ENGINEERING_CANDIDATE`; it is not a frozen decision time.

Any capture after the candidate decision time is diagnostic-only and is not primary-candidate eligible. No historical actual pre-race snapshot is asserted by this contract.

Historical parser fixtures use `availability_status=HISTORICAL_FIXTURE_ONLY`, `snapshot_role=INITIAL`, and `target_decision_time=HISTORICAL_FIXTURE_ONLY`. They are prohibited from prospective prediction input and from `PRIMARY_CANDIDATE` promotion.

The foreground freshness probe labels every captured row `LIVE_FRESHNESS_TEST`. Its T-20/T-15/T-10/T-5 roles are `INITIAL`/`PRIMARY_CANDIDATE`/`SECONDARY`/`SECONDARY`; this observes source behavior only and does not freeze or authorize a prediction candidate.
