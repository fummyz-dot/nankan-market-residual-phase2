# MODEL_EVALUATION_FREEZE_V1 — Amendment 003
## Exact CatBoost Categorical Feature Roles

**Status:** FROZEN BEFORE FIRST Job004 FIT  
**Scope:** B0 / Primary CatBoost input roles only.

No model fit has been run. This amendment fills the remaining implementation gap and does not alter any feature set or evaluation rule.

## B0

Model feature count: **55**  
Ordered feature hash:

`0108ffaf8239a0522e5b5157c0ca388bca359866375f704a0d4b42937569b5f6`

CatBoost categorical features: **7**

```text
venue
race_type
surface
direction
jockey_affiliation
trainer_affiliation
sex
```

Categorical-list SHA-256:

`8b74230a010f524d681a26f8741971e408ef9a072bb98b8a9ac6335c7ec79bf2`

All other **48** B0 features are numeric.

## Primary

Model feature count: **129** after excluding only `class_group_no`.

Ordered feature hash:

`f2d11d6632c94c3826343f5ce3051ebb9d21d26b2c5754ea38a6f06c20604aa5`

CatBoost categorical features: **9**

```text
venue
race_type
surface
direction
jockey_affiliation
trainer_affiliation
sex
class_code
age_condition_code
```

Categorical-list SHA-256:

`b9ac30f3298710cb303160f0c8e016abdfbaf9b6b9b2d07d3f60535af2a6ed73`

All other **120** Primary model features are numeric.

## Categorical preprocessing

For categorical columns only:

1. missing/null -> `__MISSING__`
2. cast to string
3. pass the exact feature-name list above to CatBoost `cat_features`

Do not infer `cat_features` from pandas dtype.

Do not one-hot encode, ordinal-encode, target-encode, frequency-encode, or remap categories.

## Explicitly numeric

The following remain numeric even though some are discrete-coded:

```text
calendar_month
day_of_week
race_number
distance_m
frame_number
horse_number
racing_age
class_ordinal
class_known_flag
mixed_class_flag
sex_restriction_flag
speed_cold_start_flag
pace_closing_cold_start_flag
```

`class_group_no` is not numeric/categorical for Primary because it is excluded from the model input entirely.

## Hard preflight

Before any B0 or Primary fit:

### B0

- feature count = 55
- ordered feature hash matches
- categorical count = 7
- categorical list/hash matches
- every categorical value is a non-null string after preprocessing
- all remaining 48 features are numeric-compatible
- no other string/object/category model column exists

### Primary

- feature count = 129
- ordered feature hash matches
- categorical count = 9
- categorical list/hash matches
- every categorical value is a non-null string after preprocessing
- all remaining 120 features are numeric-compatible
- no other string/object/category model column exists

Failure:

`JOB004_BLOCKED_CATBOOST_INPUT_ROLE_INCONSISTENCY`

Do not fit a model after a failed role preflight.

