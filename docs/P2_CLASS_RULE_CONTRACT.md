# P2 Class Rule Contract

## Scope
`P2_CLASS_RULE_V1` maps only `NANKAN_TARGET` historical races into a raw-semantic-preserving institutional race/class context. It does not map other-flat venues, calculate empirical strength, or generate model features.

## Ruleset evidence
- `NANKAN_LEGACY_PRIZE_BASED`: prize-accumulation era through 2023-03-31; historical threshold reconstruction is `UNRESOLVED`.
- `NANKAN_POINTS_2YO_PILOT_2023`: 2-year-old races from 2023-04-01 through 2023-12-31.
- `NANKAN_POINTS_ALL_HORSES_2024`: all South Kanto horses from 2024-01-01.

The 2023 and 2024 boundaries are based on archived official notices. They are not inferred from raw-condition distributions.

## Canonical vocabulary
`A1, A2, B1, B2, B3, C1, C2, C3` have order-only ordinals 8 through 1. Mixed classes retain all codes, top/bottom codes, and span. No average ordinal or continuous class strength is created.

## Safety
Historical program points, class-boundary position, and program-point boundary deltas are `NOT_AVAILABLE_ASOF_HISTORICAL`. Prize totals are not substituted. Groups are parsed only as raw tokens/numbers and have `UNVERIFIED` comparability. Grade, age, sex, weight, exchange, and special-race taxonomy remain separate from general class.

## Eligibility draft
The original helper excluded explicit newcomer, explicit JRA exchange, and safely parsed C3/below races; ambiguous/non-class age-conditioned cases were `REVIEW_REQUIRED`. It is preserved as an artifact but is `SUPERSEDED_FOR_PRIMARY_BY_P2_PRIMARY_RACE_UNIVERSE_V1`. The frozen Primary-universe contract does not remove C3 history.

## Temporal/prohibited use
Mapping never reads odds, payout, performance, or outcome fields. Any later historical feature builder must use `history.race_date < target.race_date`; same-day remains prohibited.

## Empirical-strength separation (P2-M03A)
`P2_CLASS_EMPIRICAL_MAIN_V1` is a separate, continuous, strict-as-of
Bradley–Terry rating protocol. It uses completed `NANKAN_TARGET` result rows
only for historical state updates, while this contract's A1–C3 ordinal remains
an institutional order and never a continuous-strength claim. Its frozen
configuration and result-status safety rule are defined in
`P2_CLASS_EMPIRICAL_RATING_CONTRACT.md`,
`P2_CLASS_EMPIRICAL_SELECTED.yaml`, and
`P2_EMPIRICAL_RATING_RESULT_STATUS.yaml`. The only class ablations remain
`RuleOnly` and `RulePlusEmpirical`; K variants are configuration selection, not
separate feature ablations.
