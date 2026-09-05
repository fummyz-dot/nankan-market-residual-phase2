# Phase 2 Execution Roadmap

## Phase 2A — Governance and data readiness
### P2-A01 Historical Semantic & Class Foundation Audit
No model training.
Goals:
- profile `conditions_raw` across time/venue;
- draft canonical class mapping;
- profile prize/class interactions;
- classify `lap_times_json` schema patterns;
- classify `corners_json` schema patterns;
- audit NAR <-> Keibabook race/runner joins;
- separate NAR-only pace availability from Keibabook additions;
- produce a feature-feasibility matrix.

Expected artifacts:
- `CLASS_RAW_PROFILE.csv`
- `CLASS_CANONICAL_MAPPING_DRAFT.csv`
- `CLASS_SYSTEM_VERSION_AUDIT.csv`
- `LAP_SCHEMA_PROFILE.json`
- `CORNER_SCHEMA_PROFILE.json`
- `NAR_KB_JOIN_AUDIT.csv`
- `PACE_SOURCE_COMPARISON.csv`
- `PHASE2_FEATURE_FEASIBILITY.csv`

### P2-A02 Prospective Input Contract & URL-triggered Ingestion Foundation
No predictive tuning.
Goals:
- prospective race registry independent of V1 monthly market DB;
- raw capture + hash manifest;
- whitelist current-info parser;
- odds source adapter interface;
- snapshot schema v2;
- capture/quality audit schema;
- prediction-lock schema skeleton;
- explicit operational-miss handling.

## Phase 2B — Market baseline
After prospective market data exists:
- raw market normalization;
- calibrated market;
- WIDE lower/upper baseline candidates;
- T-15 execution drift/haircut analysis.

## Phase 2C — Legacy residual
Reproduce V1 119-feature semantics and test market-offset residual use.

## Phase 2D — New NAR racing features
Recommended development order:
1. Class normalization foundation.
2. Speed.
3. NAR pace.
4. Class x speed interactions.
5. Current pre-race.
6. Conditional same-day bias.

## Phase 2E/F — WIDE/TRIO joint models
Only after shared market/snapshot and audit foundations are stable.

## Phase 2X — External information
Keibabook incremental experiments separated from main NAR+Market claim.
