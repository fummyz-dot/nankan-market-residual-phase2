# Job003B Protocol Amendment 001
## Schema Ownership / Partial Artifact Recovery

### Classification
Execution/schema correction only. **No scientific feature-definition change.**

### Root cause
The blocked Job003B attempt recomputed three Primary-only support-count columns:

- `near_distance_200m_starts`
- `same_venue_near_distance_200m_starts`
- `same_direction_distance_starts`

and attempted to carry them into the B0 writer.

That is incorrect. The B0 model schema remains exactly **55** features.  
The Primary deterministic schema remains exactly **130** features.

### Canonical schema authorities

B0:
`data/manifests/successor_v1/B0_SAFE_CORE_FEATURE_MANIFEST_V1.csv`

Primary:
`data/manifests/successor_v1/RUNNER_PRIMARY_DETERMINISTIC_FEATURE_MANIFEST_V1.csv`

A recomputed feature may be written only to a dataset whose own manifest contains that feature.

### Starter-sensitive ownership

#### B0 and inherited by Primary

- prior_starts
- starts_last_30d
- starts_last_90d
- starts_last_365d
- same_venue_starts
- same_distance_starts
- same_venue_distance_starts
- same_surface_starts
- same_direction_starts
- jockey_90d_starts
- jockey_365d_starts
- trainer_90d_starts
- trainer_365d_starts

These must be corrected in B0 and must be exactly identical in Primary for the same runner key.

#### Primary only

- near_distance_200m_starts
- same_venue_near_distance_200m_starts
- same_direction_distance_starts

These are **forbidden in B0 output**.

#### Primary race-composition only

All frozen `comp_*` features remain Primary-only and must never be inserted into B0.

### Partial artifact handling

Do not delete the blocked partial output.

1. Inventory and SHA-256 every Job003B partial artifact.
2. Quarantine under:

`audit/successor_v1/job003b/attempts/attempt_001_schema_blocked/`

3. Mark it:
   - accepted = false
   - modeling_authority = false
   - reason = `OUTPUT_SCHEMA_OWNERSHIP_MISMATCH`
4. Only after verified quarantine, clear incomplete canonical v1.1 paths.
5. Start `attempt_002` in staging.

### New staging

`data/processed/successor_v1/.job003b_attempt_002/`

Do not write directly into canonical v1.1 directories.

### Final canonical datasets

B0:
`data/processed/successor_v1/b0_safe_core_features_v1_1/`

Primary:
`data/processed/successor_v1/runner_primary_deterministic_features_v1_1/`

Expected for each:

- races = 21,560
- rows = 244,160 actual starters
- duplicate runner keys = 0

Feature contract:

- B0 = 55 features
- Primary = 130 features
- feature names/order/hashes unchanged from Job003

### Required pre-write schema assertion

Before opening any final/staging dataset writer:

1. materialized B0 model-feature names == B0 manifest ordered names exactly
2. materialized Primary model-feature names == Primary manifest ordered names exactly
3. no extra model-feature columns
4. no missing model-feature columns
5. no reordered model-feature columns

Failure => BLOCK **before writing any dataset partition**.

### Cross-dataset assertion

For identical `(race_key, horse_number)`:

- runner-key sets must match exactly
- every B0 feature inherited into Primary must match exactly under frozen NaN semantics
- Primary-only support columns must not exist in B0
- Primary composition columns must not exist in B0

### Scientific rules unchanged

Actual-starter semantics, strict prior-date rules, 21,560-race universe, feature definitions, missingness semantics, feature order, and all later model/evaluation freezes remain unchanged.

No model fitting, market access, package installation, or threshold tuning is authorized.

