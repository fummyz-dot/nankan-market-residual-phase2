# Stage2 Fold4 Forward Scorer Design V1

Status: **INVENTORY COMPLETE; IMPLEMENTATION DEFERRED**

## Frozen continuation

The initial Stage2 scorer uses Job004 Fold4 Primary M2 with no retraining. The fixed Fold4 race-head model and M1 probability-temperature parameters are used. The legacy FS04 178-feature live model is prohibited as a substitute.

## Exact local artifacts

- `fold4_primary_m2_model`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/models/m2_outer_fold4.cbm` — `0eab5da875ed4155c7b4f5b92c21d6b8893b821abaef18d0f69f37e20ef4ebf2`
- `fold4_primary_m2_raw_prediction`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/raw_predictions/m2_outer_fold4.npy` — `ba9db45760baf37153cae43426504f71e6ce139c094d4a9a8166995e023ee629`
- `fold4_race_head_model`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_004/checkpoints/models/race_head_outer_fold4.cbm` — `58357312e69516e57c52121ec57c64093a686e101e2d0b3ae0fc0e482e6d41ec`
- `fold4_race_head_raw_prediction`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_004/checkpoints/raw_predictions/race_head_outer_fold4.npy` — `0f1cc1bc46181ed8f4538839f621113d9f4d5424047ac8f28d675dd23d5b6df9`
- `fold4_eb_fixed_components`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/eb/fold4_components.json` — `b2e56f153e0ce0b056e3117f52e50d9e841da0e33e0831244ff67516f543bab2`
- `fold4_eb_outer_effects`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/eb/fold4_outer_fixed.npy` — `2f2e6086d1d38c39ce423f315ac676b3cdd283e7b53a8e9f64cac897f2d2c0d5`
- `fold4_eb_outer_audit`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/eb/fold4_outer_fixed_audit.csv` — `dbe4400d825d38482bfd7980cd0e297f5f8207d408456cb065caf3e0723843b0`
- `m2_inner_date_causal_effects`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/eb/m2_inner_date_causal.npy` — `46564efe080091fed727841450eddd447f6960ccd2ba38cf6fd4460c0acdac0f`
- `m2_inner_date_causal_audit`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/eb/m2_inner_date_causal_audit.csv` — `b9e581d1483c08239d392628592e563bdd6915dd7db8d92f7b494619c927b2e5`
- `m2_inner_raw_2021`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/raw_predictions/m2_to_2021.npy` — `1e33bc21674efb67ea1721a73d0aea06c94969d976deed66c2a80fd178de7606`
- `m2_inner_raw_2022`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/raw_predictions/m2_to_2022.npy` — `d4d4d38c1370e4f021ce2997e0d2a8172794abd0ad49bb0c2cf40345a3b3b041`
- `m2_inner_raw_2023`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/raw_predictions/m2_to_2023.npy` — `31e75c08156cfc35b9b2626f286c03c0070c3da3f29aa98410e4fee745169a95`
- `m2_inner_raw_2024`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/raw_predictions/m2_to_2024.npy` — `01e237a0ad11ab031dc51a480b3ca89512b23120ab97ae0329220a625e4fcc4b`
- `m2_inner_raw_2025`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/raw_predictions/m2_to_2025.npy` — `4f7f0a3fede12d5c07d851948ef008374d8b809d0bb8e5279fdcfb600ca0298e`
- `job004_historical_runner_residual_source`: `/home/nabe/projects/nankan-market-residual-phase2/outputs/successor_v1/job004/oof/runner_predictions.csv.gz` — `87695b71cd25591af938757c175f809a7dda108e884a0ad47b1eecb3acf935d6`
- `primary_dataset_manifest`: `/home/nabe/projects/nankan-market-residual-phase2/data/processed/successor_v1/runner_primary_deterministic_features_v1_1/_DATASET_MANIFEST.json` — `5550b06f12a47bc85abfa889f6ac4fd1f57e047206dcbbb99a6e3fb568e787c7`
- `primary_dataset_partition`: `/home/nabe/projects/nankan-market-residual-phase2/data/processed/successor_v1/runner_primary_deterministic_features_v1_1/year=all/part-000.csv.gz` — `3ad5e47eab84e1e8f5f56ef1717e139b838d3a097d546f54aa9f1aff30a80bb6`
- `fold4_m1_parameters`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/pl_temperature_fit.csv` — `501205602fa8f5690a213682955aa7912da59cd200f89b3963ff00425d64bbeb`
- `fold4_candidate_selection`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/model_selection_by_fold.csv` — `fd97768d8ddb34950de7bcfc5e3da05b25925ac80ff0836b9ed78dc1f4828cb1`
- `primary_ordered_manifest`: `/home/nabe/projects/nankan-market-residual-phase2/data/manifests/successor_v1/PRIMARY_MODEL_INPUT_MANIFEST_V1.csv` — `eb6bf0291f55e0a4d11f01987237b82af2e36d5065de395606d06d3600923954`
- `primary_categorical_role_manifest`: `/home/nabe/projects/nankan-market-residual-phase2/data/manifests/successor_v1/CATBOOST_INPUT_ROLE_MANIFEST_V1.csv` — `1c0357fb9c8cc41554db1d9a2af75ce969e246780d4049f1a5e4a43ae00e65a5`
- `race_head_ordered_manifest`: `/home/nabe/projects/nankan-market-residual-phase2/data/manifests/successor_v1/RACE_HEAD_INPUT_MANIFEST_V1.csv` — `023d7a5d0a6570c4350f571d3a5ed5c37885fec1a42ecedf19671dcc731d484b`
- `runtime_freeze`: `/home/nabe/projects/nankan-market-residual-phase2/data/manifests/successor_v1/RUNTIME_FREEZE_V1.json` — `226c7d6bdc5e21514858a789df311cbb020415daaa5f77b584fa1550e3aa2438`
- `history_database`: `/home/nabe/projects/nankan-market-residual-phase2/reference/v1/db/nankan_history.sqlite` — `5fe7a9e88e25f64e51e39e27b789315ababfbe597786b26701f0e4a7f8486936`
- `job004_final_report`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/JOB004_FINAL_REPORT.md` — `1bc3cb731293b1f9e51908bc8b5358b24c992230fcb8b7ca6fdc52ba29d35d12`
- `job004_run_manifest`: `/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/run_manifest.json` — `c361343437beefe38df894685ea4e43748ca4169f4e744e18844ddd404e74cc3`

## Fold4 parameters

- M0 T0: `0.44022846403852645`
- M1 T0: `0.44167862602822466`
- gamma: `0.02721867845067733`
- upset mean: `0.8460234339580412`
- upset sigma: `0.054628106852266066`
- EB components: `{"horse": {"sigma2": 0.4992455155451959, "tau2": 0.015158512217699045}, "horse_x_venue": {"sigma2": 0.470814153837799, "tau2": 0.0}, "jockey": {"sigma2": 0.4792335501613323, "tau2": 0.0007246178187321685}, "jockey_x_venue": {"sigma2": 0.4797633100525053, "tau2": 0.0004513917109325973}}`

## Dataflow

1. Resolve the actual T15 pre-race active roster and identities from the prospective store/current capture.
2. Query normalized history strictly before `target_race_date`; same-day history is excluded.
3. Reuse the frozen Job003B feature builders to emit the exact ordered Primary129 row set and its race-constant RaceHead32 projection.
4. Load the fixed Fold4 M2 and race-head CatBoost artifacts; generate Fold4 M1 PL probabilities with the frozen T0/gamma/upset standardization and the n=3 M0 rule.
5. Join only the eligible exact-T15 WIDE pair universe. Freeze an immutable prediction artifact before any target result access.
6. After every race on a date is frozen and the date settles, official outcome reconciliation may append that date's residuals for a later date only.

## Date-causal EB lifecycle

At the beginning of date `d`, collect residual observations whose source race date is strictly `< d`. Rebuild from zero in layer order `horse`, `jockey`, `horse_x_venue`, `jockey_x_venue` with Fold4 fixed sigma2/tau2, at most 20 cycles, and the `1e-5` convergence rule. Unknown horses, jockeys, and interactions contribute zero until a prior-date observation exists. Score and freeze all races on `d`; never update state between same-day races. Only after the date settles may its residuals become input for the next date.

## Feature materialization sources

- Base/Primary/race-composition functions: `src/audit/p2s_job003_materialized_feature_foundation.py`
- Actual-starter correction and 130-to-129 selection lineage: `src/audit/p2s_job003b_actual_starters.py`
- Actual T15 roster/card/identity input adapters: `src/operations/live_feature_materializer.py`
- Strict-as-of history: `src/features/online/history_view.py` and `src/features/online/normalized_history_provider.py`
- Later normalized history delta: `src/operations/build_normalized_live_history_delta.py`

Every feature primitive must be resolved under the frozen Job003B semantics; missing inputs fail closed. `first_seen_date`, `last_seen_date`, market values, current results, and same-day history are prohibited.

## Component readiness

- Primary 129 feature materialization: `READY_WITH_ADAPTER` — Reuse Job003B pure feature builders plus T15 active-roster and strict-as-of normalized-history adapters; no end-to-end online 129 builder exists.
- race-head 32 feature materialization: `READY_WITH_ADAPTER` — Exact manifest is a race-constant subset of Primary129; requires the same post-cutoff adapter.
- Fold4 M2 loading: `READY_EXISTING` — Exact CatBoost binary loads with the frozen 129-feature order.
- Fold4 race-head loading: `READY_EXISTING` — Exact CatBoost binary loads with the frozen 32-feature order.
- Fold4 PL probability generation: `READY_WITH_ADAPTER` — Job004 exact distribution and M1 parameters are reusable; a forward orchestration module is absent.
- EB state reconstruction through 2026-07-31: `READY_WITH_ADAPTER` — Fixed components, inner/outer predictions, targets, keys, and history lineage exist; reconstruct-and-persist adapter is absent.
- EB post-cutoff date-causal continuation: `READY_WITH_ADAPTER` — Dynamic-key reference supports unseen groups and fixed-component rebuild; date lifecycle orchestrator is absent.
- T15 WIDE market join: `READY_WITH_ADAPTER` — JOB005 contract/store rows establish exact eligible pair universe; scorer join adapter is absent.
- immutable pre-result prediction artifact: `READY_WITH_ADAPTER` — Existing pre-result freeze transaction pattern is reusable but is bound to legacy178 and requires a Stage2 schema adapter.
- later official outcome reconciliation: `READY_EXISTING` — Official result collection, immutable frozen-decision linkage, and reconciliation operations already exist; JOB006 does not invoke them.

## Prediction artifact schema

The next job must freeze an immutable artifact containing schema/version, race identity and date, scheduled post/decision timestamps, current and WIDE capture ids/hashes, active roster identity, Primary129 and RaceHead32 ordered hashes, model/race-head/EB artifact hashes, Fold4 M0/M1/gamma/upset parameters, per-runner model probabilities, exact WIDE pair `q_model`, and result-boundary flags. It must contain no target result, payout, CE, delta, ROI, or profit.

## Result-access barrier

Prediction generation must open only pre-race inputs. A content-hashed prediction artifact must be durably frozen for every race on date `d` before an outcome connector for `d` is allowed. Reconciliation is a later phase and may update EB state only for a future date.

## Next implementation modules

- A post-cutoff exact Primary129/RaceHead32 materialization adapter.
- A Fold4 fixed-model/PL forward inference adapter.
- A date-batched fixed-component EB reconstruction/state module.
- A Stage2 immutable prediction freezer and later result-reconciliation evaluator with a hard access barrier.
