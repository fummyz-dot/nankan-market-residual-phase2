# P2 Feature Integration Contract

The historical P2 Main matrix has one row per South Kanto historical runner, 2020-01-01 through 2026-07-31. Model columns are namespaced as `V1__`, `P2_CLASS_RULE__`, `P2_CLASS_EMPIRICAL__`, `P2_CLASS_UNCERTAINTY__`, `P2_SPD__`, and `P2_PACE__`.

All joins require exactly one row on `race_key + horse_identity_key + horse_number`; missing values retain source-contract semantics and are never zero-imputed. Eligibility is metadata only. Labels/outcomes, Market, P2_CURRENT, P2_BIAS, and Keibabook/P2_EXT fields are physically excluded.

Frozen pre-performance candidate sets are `FS00_LEGACY`, `FS01_LEGACY_SPD`, `FS02_LEGACY_SPD_PACE`, `FS03_LEGACY_SPD_PACE_CLASS_RULE`, and `FS04_LEGACY_SPD_PACE_CLASS_FULL`. No extra subset is authorized without a new versioned protocol.

`P2_SPD` and `P2_PACE` remain `PROVISIONAL_DEVELOPMENT_FEATURE`; this integration does not elevate them.
