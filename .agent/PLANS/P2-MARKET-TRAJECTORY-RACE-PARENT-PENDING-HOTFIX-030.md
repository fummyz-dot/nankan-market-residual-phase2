# P2-MARKET-TRAJECTORY-RACE-PARENT-PENDING-HOTFIX-030

## Scope

Separate observer race-parent cardinality from true duplicate-parent integrity
failure.  This is limited to the WIN market trajectory / Lead-Lag observers,
their race-day rendering, focused tests, and narrow operations documentation.

## Inputs

- `market_snapshot.sqlite` pre-race market captures
- `live_development.sqlite` immutable evidence race parent and sidecar ledgers
- Existing frozen trajectory / Lead-Lag bundles

## Frozen invariants

- `0` evidence parents is pending and performs no sidecar write.
- `1` parent preserves current processing.
- `2+` parents remains the existing fail-closed `*_RACE_NOT_UNIQUE` path.
- Main scheduling, T15/fallback, mark semantics, scientific units, FS04, model,
  policy, result/payout access, and accounting do not change.

## Validation

- Focused trajectory and Lead-Lag cardinality and idempotency tests.
- Race-day pending/rendering and Main-state isolation tests.
- Existing trajectory/Lead-Lag fresh-process regressions.
- Compileall with the production venv.
