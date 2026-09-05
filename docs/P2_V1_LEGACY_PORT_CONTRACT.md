# P2 V1 Legacy Port Contract

`P2_V1_LEGACY_V1` ports exactly the frozen 119 V1 pre-race features in groups `F0`, `F1`, `F2`, `F3`, `F5`, `F6`, `F7`, and `F8`; `F4` is absent. The authoritative active list is `configs/features/P2_V1_LEGACY_FEATURE_LIST_V1.yaml`.

All rolling sources require `source_race_date < target_race_date`; same-calendar-date result use is prohibited. Current target outcomes, current body weight, odds, popularity, payout, Keibabook, `horses.last_seen_date`, post-cutoff rows, and bespoke Speed/Pace values are prohibited.

The active builder is `src/features/legacy_v1/`. It never imports V1 runtime code. Its frozen static-sex map is a Phase 2 artifact derived once from immutable V1 horse semantics and is used only to reproduce the recorded V1 categorical semantic.

V1 parity uses immutable artifact overlap keyed by race identity and horse number. The broader Phase 2 historical-development roster is retained without labels.
