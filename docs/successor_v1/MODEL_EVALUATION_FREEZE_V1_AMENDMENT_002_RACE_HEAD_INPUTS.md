# MODEL_EVALUATION_FREEZE_V1 — Amendment 002
## Exact Race-Head Inputs

Status: FROZEN BEFORE FIRST Job004 FIT.

Race-head unit: one row per race, sourced only from `runner_primary_deterministic_features_v1_1` actual-starter rows.

Feature count: **32**

Ordered feature SHA-256:
`d65c205307ea63b58b3f284530d6daa747f04bb3411c068c3430735860a11303`

## Ordered features
```text
calendar_month
day_of_week
venue
race_number
race_type
surface
direction
distance_m
log_prize_1
log_prize_total
class_code
class_ordinal
class_known_flag
mixed_class_flag
age_condition_code
sex_restriction_flag
comp_ability_mean
comp_ability_sd
comp_ability_top3_mean
comp_ability_gap_1_2
comp_ability_gap_3_4
comp_ability_coverage
comp_speed_mean
comp_speed_sd
comp_speed_top3_mean
comp_speed_coverage
comp_front_propensity_sum
comp_front_propensity_max
comp_front_propensity_sd
comp_history_coverage_mean
comp_uncertainty_mean
comp_uncertainty_sd
```

Categorical:
```text
venue
race_type
surface
direction
class_code
age_condition_code
```

Numeric missing stays NaN; categorical missing is `__MISSING__`; no imputation or scaling before CatBoost.

For every race, every selected feature must be constant across actual-starter rows. All-NaN numeric values are valid; mixed NaN/non-NaN is invalid; finite numeric tolerance is `1e-12`. Never average inconsistent replicated values.

Forbidden: field_size/starter_count/runner_count, runner-level identity or history outside frozen `comp_*`, B0/Primary predictions, EB, PL, market/odds/payout, current outcome, same-day results, first_seen/last_seen.

Target:
`U_r=-log(max(P0(actual unordered Top3 set),1e-12))/log(C(n_r,3))`
with strict-OOF B0 PL P0.

Cross-fit:
- 2021 structural label uses T0=1.0
- y>=2022 structural label uses B0 T0 fitted only on earlier inner-OOF years
- race-head prediction 2021 unavailable
- y>=2022 prediction trains only on prior structural-label years
- outer VALID trains on all outer-TRAIN structural-label races

CatBoost is fixed: Huber delta 1.0, iterations 400, depth 4, lr 0.03, l2 20, seed 260904, random_strength 0, bootstrap No, SymmetricTree, Plain, has_time True, thread_count 1.

M1 standardization uses only eligible inner-OOF race-head predictions from years 2022..outer TRAIN end:
`mu=mean(upset_score)`, `sigma=std(ddof=0)`, `z=clip((score-mu)/sigma,-3,3)`.
If sigma<1e-12, M1 unavailable and gamma=0. Outer VALID never recomputes mu/sigma. R1 uses raw upset_score.

