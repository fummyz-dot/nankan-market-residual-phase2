# P2_CLASS Contract Draft

> Status: P2-M02's official class-rule protocol is incorporated by
> `P2_CLASS_RULE_CONTRACT.md`, and P2-M03A's empirical protocol is incorporated
> by `P2_CLASS_EMPIRICAL_RATING_CONTRACT.md`. This retained draft cannot
> override either versioned contract.

`P2_CLASS_RULE` is a deterministic, non-ordinal decomposition of `conditions_raw`, `race_type`, and observed race-name tokens. It outputs only normalized text, age/sex/weight scopes, observed class/grade tokens, and mapping-review status. It must not output class strength, class delta, target/outcome-derived values, or same-day bias.

## Incorporated by P2-M02

The implementation-level contract is now `docs/P2_CLASS_RULE_CONTRACT.md`. P2-M02 adds official-source-backed, date/age-scoped ruleset assignment and an order-only A1–C3 ordinal representation. That ordinal is institutional order only, not continuous strength. This draft remains retained for the empirical-strength non-approval below.

## Future empirical-strength design (not implemented)

A future `Rule+Empirical` ablation requires: a frozen target definition; only historical completed races with evidence `available_at <= decision_time`; race-level/runner-level grain declared before aggregation; a policy for new classes, new venues, sparse class cells, transfers, and text-regime changes; shrinkage/uncertainty intervals; and a separate protocol plus untouched holdout. `RuleOnly` and `Rule+Empirical` are the only registered future class ablation candidates. No empirical value is calculated in P2-A01.
