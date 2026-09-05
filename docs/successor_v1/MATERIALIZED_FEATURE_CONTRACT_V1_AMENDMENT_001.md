# MATERIALIZED_FEATURE_CONTRACT_V1 — Amendment 001
## Actual-Starter Semantics

### Why this amendment exists

Job004A detected, before any model fit, that Job003 `P1_RACE_COMPOSITION`
included retained nonstarter rows. The example `20200127_KAWASAKI_11`
changed `comp_ability_mean` materially when recomputed on actual starters.

This is a source-semantics correction, not a performance-driven feature change.

## Authority

Use the repository's existing audited `starter_status` classifier.

Actual starters:

- `STARTER_VALID_FINISH`
- `STARTER_NO_VALID_FINISH`

Cancellation/exclusion/resolved nonstarter rows are not starters.

Any unresolved result status remains a hard block.

## Historical start counts

A historical row contributes to any feature whose semantics mean "starts"
only when it is an actual starter and:

`source_race_date < target_race_date`

This applies to all frozen support-count features listed in the JSON authority.

## Race composition

Every `P1_RACE_COMPOSITION` aggregate must be recomputed using **current-race
actual starters only**.

Do not include retained cancellation/exclusion/nonstarter rows.

The historical roster remains a proxy for T15 roster timing; this amendment
does not change that warning.

## Dataset versioning

Do not overwrite Job003 v1 artifacts.

Mark them:

`SUPERSEDED_FOR_MODELING`

Create new canonical training datasets:

- `b0_safe_core_features_v1_1`
- `runner_primary_deterministic_features_v1_1`

Expected:

- races = 21,560
- rows = 244,160 actual starters
- B0 features = 55
- Primary deterministic features = 130

Feature names/order do not change, therefore the ordered-name hashes remain:

B0:
`0108ffaf8239a0522e5b5157c0ca388bca359866375f704a0d4b42937569b5f6`

Primary:
`d4ccb75419a50d70bee7fd037f576a48be7dce7d4bb18b388df43fa8bcac0e82`

## Reuse rule

Existing Job003 values may be carried into v1.1 only when:

1. the feature is mathematically independent of starter membership, or
2. strict recomputation under the amended semantics matches exactly.

Starter-sensitive support counts and every race-composition feature must be
recomputed.

## No modeling

This amendment authorizes materialization/audit only. No model fit, market
access, threshold tuning, or runtime package installation is authorized.

