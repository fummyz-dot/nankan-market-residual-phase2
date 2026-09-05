# Project State

## Current phase
`P2_OHI_20260902_POST_LIVE_AUDIT_023_COMPLETE`; the next work is the
design-only `P2-RACE-DAY-PERFORMANCE-DESIGN` process, not an optimization
implementation task.

## Current verified operating state

- FS04 remains frozen at 178 features with ordered hash
  `ff1d6714be9cf889d8949105c1aa81c989e2867886ec7446ed4ef1a22ebc6cb2`.
  `DEV-LIVE-V1` remains
  `fb7a4b8535dbdd295a0a7c6b1527e71acbbe14d6a239a0e676bae06f0602c637`.
- Main WIN policy is unchanged: model probability >= .015, model/calibrated
  Market >= 1.25, and GER >= 1.15.  Main WIDE remains
  `DISABLED_RESEARCH_ONLY`.
- Ohi WIDE Experimental V0 remains manual-purchase only: ¥100 per race,
  at most two purchased races / ¥200 per day, `T15_STANDARD` only.  Its hard
  action cutoff is 300 seconds; >=480 seconds is `COMFORTABLE`, 300--480
  seconds is `MARGINAL`, and <300 seconds suppresses an otherwise manual-buy
  candidate as `NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED`.
- 2026-09-02 大井 (5R, 6R, 7R, 8R, 10R, 11R, 12R) reached
  `scientific_day_complete=true` and `actual_accounting_complete=true`, but
  terminated as `DAY_COMPLETE_HISTORY_PENDING` / `BLOCKED_RECOVERABLE` /
  exit 10 / `safe_to_resume=true` / `user_action_required=false`.
  Scientific day completion and Actual Accounting completion therefore do not
  imply that all model-history readiness is complete.
- The completed post-live audit is
  `P2_OHI_20260902_POST_LIVE_AUDIT_023_COMPLETE`:
  [report](../audit/reports/P2_OHI_20260902_POST_LIVE_AUDIT_023.md) and
  [run manifest](../audit/data/p2_ohi_20260902_post_live_audit_023/run_manifest.json).
  It records two confirmed actual WIDE purchases, turnover ¥200, payout ¥0,
  P&L -¥200, ROI -100%, and a functioning daily cap.  Its descriptive
  near-miss and Price Shadow observations do not authorize retrospective
  model, threshold, or policy adjustment.

## Verified operational hotfixes

- `P2_RESULT_COMPLETENESS_IDEMPOTENCY_HOTFIX_016`: identical official raw
  SHA retrieved at another local archive path is `IDEMPOTENT_NOOP`; the
  append-only semantic conflict and fail-close protections remain.
- `P2_RACE_DAY_COMPACT_POST_WAITING_HOTFIX_017`: a valid
  `POST_RACE_WAITING` payload no longer crashes compact rendering; the
  0/10/20 exit contract is unchanged.
- `P2_OHI_WIDE_MANUAL_ACTIONABILITY_GUARD_HOTFIX_019`: 300-second hard
  cutoff, 480-second comfort boundary, late-candidate evidence preservation,
  and observational WIDE child stage timing are verified without changing
  science, model, or policy.
- `P2_KEIBABOOK_CONTEXT_ONLY_NONBLOCKING_HOTFIX_020`: Ability/Training
  source missing or parse failure is explicit unavailable context and cannot
  alone block Main; genuine Main-required failures remain fail-closed.
- `P2_RESUME_RESEARCH_PROVENANCE_HOTFIX_022`: idempotent WIN/CURRENT resume
  returns durable T15 provenance again, without changing evidence IDs,
  hashes, or numerical payloads.

## Completed jobs
- `P2-A00 Setup Preflight & Workspace Normalization` — completed; `READY_FOR_P2_A01`.
- `P2-A01 Historical Semantic & Class Foundation Audit` — completed; semantic and availability contracts are draft-only.
- `P2-A01R History Cutoff Provenance Resolution` — completed; 128 post-cutoff rows have unresolved raw-byte provenance but are structurally isolated from the historical-development partition.
- `P2-A02A Prospective Input & Snapshot Foundation` — completed; source-agnostic prospective capture, quarantine, manifests, and supervision contracts are operational with local synthetic validation.
- `P2-A02B-1 Nankan Official Historical Fixture Adapter` — completed; the retained 2026-07-31 川崎10R fixture parses safely but remains `HISTORICAL_FIXTURE_ONLY`.
- `P2-A02B2-PREP Live Freshness Probe Implementation` — completed; a foreground T-20/T-15/T-10/T-5 observer is validated with retained fixtures and mocked clocks only.
- `P2-A02B-3 End-to-End Race Analysis Bundle Foundation` — completed; the retained 2026-08-19 川崎5R T-15 capture and daily Keibabook JSON produce a source-separated, no-model/no-ticket bundle.
- `P2-M00 14-Venue Horse Identity & Historical Context Foundation` — completed; a raw-native exact `馬名 + 生年月日` composite is established for audited flat-history completeness only. Ban'ei is excluded and `P2_XVENUE` remains unapproved for model use.
- `P2-M01 Full Flat-NAR Historical Context DB Build` — completed; `db/p2_history_context.sqlite` contains the 2020-01–2026-07 South Kanto plus other-flat raw-semantic-preserving corpus with complete archive/member provenance.
- `P2-M02 Official Class Ruleset & Race Condition Canonicalization Foundation` — completed; official-source-backed ruleset assignment and Nankan-only class/taxonomy canonicalization are available, with no empirical strength or historical program-point reconstruction.
- `P2-M03A Empirical Class Strength Rating Protocol & Configuration Freeze` — completed; South-Kanto-only strict calendar-date-block pairwise Bradley–Terry configuration `R3` (`K=1.00`) was selected solely on 2021–2024 race-equal pairwise log loss and validated once on 2025. Other-flat, Ban'ei, and exchange updates remain prohibited.
- `P2-M03B Strict-As-Of Empirical Class Strength Feature Build` — completed; frozen `R3` pre-ratings were independently rebuilt with M03A parity, deterministic runner/race empirical class datasets, prior-date context/race deltas, and separate information metadata. Market sources remain unused and P2_XVENUE remains unapproved.
- `P2-M04A Strict-As-Of Standard Time & Speed Figure Protocol Freeze` — stopped at the registered validation gate: S3 was selected only on 2021–2024, but its 2025 MAE did not beat the fixed course-only reference. No additional speed search was run.
- `P2-M04R Speed Protocol Amendment & Course-Only Baseline Freeze` — completed; `P2-AMEND-001` preserves M04A's failed going-adjusted S3 record and separately freezes the pre-specified all-history course-only reference as a provisional development speed standard. No new speed search was added.
- `P2-M04B Strict-As-Of Runner Speed History Feature Build` — completed; M04R course-only observations match exactly, and 250,093 date-block pre-race P2_SPD feature rows were created. The block remains provisional pending new prospective development evidence.
- `P2-M05A NAR Pace / Lap / Corner Semantic & Parser Freeze` — completed; NAR last-3F and exact race-level lap/pace observations are ready for history construction. NAR runner corners remain raw-group-order only and are not model-ready.
- `P2-M05B Strict-As-Of NAR Main Pace History Feature Build` — completed; 250,093 deterministic date-block pre-race `P2_PACE_MAIN_V1` rows were built from non-exchange South Kanto runner last-3F-relative and exact race-level pace-balance observations only. Runner corners, runner first-3F, Keibabook, other-flat, Speed/Class adjustment, and Market inputs remain excluded.
- `P2-M06 V1 Legacy Semantic Port & Unified Historical Feature Matrix Foundation` — completed; exact 119-feature V1 active port has zero-difference immutable-artifact parity across 245,208 overlap rows. All 250,093 South Kanto development-roster runners are joined one-to-one with P2_CLASS, P2_SPD, and P2_PACE in a deterministic, label-free 178-column matrix. FS00–FS04 are frozen before performance work.
- `P2-M07 Primary Target Universe & Market-Offset Model Foundation` — completed; all 21,849 races are development-frozen by pre-race-only Primary universe rules, while all 250,093 runner outcomes are retained separately with explicit WIN soft-tie semantics. The future WIN market-offset race-softmax form and FS00–FS04 connection are frozen; no Market data or model fitting occurred.
- `P2-M08A WIN Market Baseline Normalization & Calibration Protocol` — completed; strict WIN inverse-odds q normalization, capture-time roster rules, incomplete-snapshot rejection, and deterministic all-venue power-gamma calibration method are frozen. Historical `MARKET_TIME_UNKNOWN` gamma is engineering diagnostic-only; prospective stabilization remains outcome-free and T-15 remains unfrozen.
- `P2-M08B LightGBM Market-Offset Race-Softmax Backend Foundation` — completed; LightGBM 4.7.0 is the sole frozen H1 backend with native Market init-score race softmax, exact gradient, frozen diagonal Hessian approximation, FS00-only 833-race/9,522-runner frame, nested walk-forward protocol, and six registered legacy configurations. Only engineering fixtures were fitted; residual performance evaluations remain zero.
- `P2-M09` — pre-formal evaluation incident recorded as `P2-INC-001`; no registered six-config outer evaluation, selection, bootstrap, or formal search-budget consumption occurred. M09R controls whether unchanged resumption is authorized.
- `P2-M09R Protocol Incident Recovery & Outer-Validation Integrity Audit` — completed; `P2-INC-001` is preserved and bounded to the March-to-April inner-validation probe. M08B frozen config/objective/FS00 hashes reconcile, May–July formal outer validation is untouched, no post-peek adaptive modeling change is recorded, formal budget remains 0/6, and M09 is authorized to resume unchanged only with its explicit formal-execution guard.
- `P2-M09-RESUME Formal H1 Legacy Residual Development Evaluation` — completed; the first formal registered six-config evaluation after `P2-INC-001` used all 18 frozen config-fold checkpoints and exhausted formal H1 budget 6/6. `H1-C06` was selected by the fixed pooled race-equal rule, with a positive delta versus calibrated Market; status is `H1_HISTORICAL_NO_SIGNAL`. This historical `MARKET_TIME_UNKNOWN` result is development-only, does not confirm probability edge or freeze T-15/gamma, and does not block H2.
- `P2-M10 H2 NAR Racing-Information Historical Development Evaluation` — completed; all four preregistered NAR-core candidates used the unchanged H1-C06 backend, M09 folds, and exact M09 fold-gamma values. FS04 remained the pre-designated NAR-core candidate rather than a post-hoc choice among ablations. Its pooled delta versus calibrated Market was positive (`+0.0023155723053499225`), hence `H2_NAR_CORE_HISTORICAL_NO_SIGNAL`. H2 budget is 4/6; P2_CURRENT remains independently prospective and H2-C06 unallocated. This historical `MARKET_TIME_UNKNOWN` evidence is not a fresh holdout and does not confirm probability edge, T-15, or Primary gamma.
- `P2-M11A Current Pre-Race Information & Prospective Stabilization Foundation` — completed; CUR01–CUR06 are preregistered source-quality candidates, separate provenance-complete current-info snapshot tables and an official-only foreground day collector are implemented, and an outcome-free stabilization dashboard is operational. Existing 2026-08-19 Kawasaki 5R T20/T15/T10/T05 capture parity passes, but the captures were several seconds after their nominal marks and are not promoted as T15 availability evidence. Stabilization is not ready; H2-C05 remains unevaluated, H2-C06 unallocated, and T15 unfrozen.
- `P2-M11A-R Stabilization Gate Amendment & Pre-Decision Capture Timing Fix` — completed; retained `P2_STABILIZATION_GATE_V2` replaces the 4-week/200-race engineering minimum with 14 days/80 distinct eligible predecision-valid races/four venues/at least 10 distinct valid eligible races per venue before any outcome or performance use. T15 proof is now only a capture in the 60 seconds ending at decision time; requests begin 30 seconds before the mark, and late raw captures are preserved but excluded from coverage and P2_CURRENT activation. The 2026-08-19 Kawasaki fixture remains unchanged and honestly non-proving for T15.
- `P2-M11A-S Prospective Collector Observability & Fail-Fast Safety` — completed; non-capturing preflight, read-only live status, immediate atomic race/day status, heartbeat, event, resume, and race-scoped/day-fatal artifacts are available. The 2026-08-20 official preflight discovered 12 races and generated schedules without capture. No outcome, performance, or ROI data was accessed.
- `P2-M11A-S-HOTFIX01 Live Collector FK / Failure-State Hotfix` — completed; `P2-OPS-001` preserves the 2026-08-20 Kawasaki 1R T20 FK failure. The archive capture UUID is now registered as the FK parent before child rows in an explicit transaction; foreign keys remain enabled. Failed captures are no longer success checkpoints/events or `last_completed` values. No outcome or performance data was accessed.
- `P2-M11A-S-HOTFIX02 Compact Human Collector Status` — completed; the read-only status command now defaults to compact HEALTHY/WARNING/ERROR monitoring, with `--verbose` and `--json` alternatives and 0/1/2 exit codes. Future WAITING is normal and retained P2-OPS-001 is visibly historical rather than fatal. Collector/capture/timing/DB behavior is unchanged.
- `P2-M12A Live Development Decision Ledger / Official Result / Reconciliation Foundation` — completed; an FK-enforced isolated live ledger now preserves immutable pre-post decision freezes separately from official result/payout captures. Official 2026-08-20 Kawasaki 6R–11R result/payout smoke collection is provenance-complete and idempotent, while all six races are deterministically `NO_PRE_RACE_DECISION`; no model performance or ROI was evaluated.
- `P2-M12B Online Historical Feature Materialization / Development Shadow Pipeline` — blocked before implementation: approved current pre-race sources do not carry the `horse_name + birth_date` tuple required by `P2_HORSE_IDENTITY_V1`. Name-only lookup is prohibited, so an FS04 live feature vector, shadow model inference, and prediction freeze cannot be safely claimed.
- `P2-M12B-R1 Official Pre-Race Horse Identity Source Recovery` — completed; saved official T15 predecision card raw carries an official horse-detail link for every runner. I2 full-date detail verification validates the card's short date without heuristic expansion. Kawasaki 6R–11R has 69 exact historical identities, one genuine cold start, and zero unresolved/collision; M12B may resume its online feature materialization stage.
- `P2-M12B-RESUME Online Inference / Prospective Shadow E2E Pipeline` — stopped at P1 reuse/source audit. Identity is no longer a blocker, but the saved official current card exposes course layout `外` rather than the V1-required `direction` (`左`/`右`). No venue/layout inference was performed, so exact FS04 online parity, model build, prediction, and replay were not started at that point.
- `P2-M12B-R2 Official Course Direction Mapping Contract` — completed; raw-archived official South Kanto course metadata now freezes D1 explicit-pre-race / D2 official-static / D3 block direction resolution. Kawasaki, Funabashi, and Urawa resolve left; Ohi uses a strict official distance allow-list (1650 left and only the listed other distances right). Historical QA has zero mapped-direction mismatches, and all saved 2026-08-20 Kawasaki 6R–11R T15 cards resolve left without using their `外` layout token. No model, result, performance, payout, or ROI path was accessed.
- `P2-M12B-RESUME2` — stopped at the mandatory `P2_ONLINE_CLASS_24` gate. `P1_ONLINE_V1_119` passed exactly (55 fake-live fixture runners, 119 fields, maximum numeric difference 0). The class adapter's multi-fixture harness produced 131 mismatches because virtual earlier fixture races did not apply their later historical rating update for subsequent fixture dates. No parity tolerance, feature definition, model, search, Market, or outcome rule was changed; P3–P11 did not start.
- `P2-M12B-R3 Online Class Strict-As-Of Replay Harness Recovery` — P2 Class (24), P3 Speed (15), P4 Pace (20), and P5 FS04 (178) parity passed without changing frozen semantics; the prior failed P2 artifacts remain preserved. `DEV-LIVE-V1` was deterministically trained with H1-C06, 833 development races, and the existing M10 H2-C04/WF3 19-tree horizon. P7 is blocked before live inference because the history context ends at 2026-07-31 and cannot yet prove use of approved post-July strictly-prior history for a later live target.
- `P2-M12B-R7 Exact Official Pedigree Crosswalk for Nonstarter Identity Recovery` — completed; static official detailed-card `horse_name + sire + dam + damsire` is a narrow I2 fallback to exactly one official-derived canonical master identity when the direct official horse-detail anchor is absent. The canonical master has 43,544 complete tuples and zero collisions; a 100-runner hidden-detail-ID simulation had zero wrong identities. The blocked Urawa 2R nonstarter resolves uniquely.
- `P2-M12B-R8 Official Starter-No-Valid-Finish Semantic Recovery` — completed; frozen M07 semantics already map raw `競走中止` to `STARTER_NO_VALID_FINISH`. Historical precedent (921 runners / 870 races) and five later-start FS04-178 parity fixtures (65 rows, zero mismatch, maximum numeric difference `5.000444502911705e-13`) establish the exact live representation: starter retained, `finish_position`/finish time/last-3F NULL, and no invented finish. Urawa 2026-08-07 R6 committed atomically. R4 then resumed and is now stopped separately at 2026-08-05 Funabashi R10: current official card names use an unapproved `[J]` designation while horse-detail names are canonical bare names. No normalization was inferred.
- `P2-M12B-R9 Official Runner Affiliation Prefix Semantic Recovery` — completed; the fixed 204-card official audit observed only `[J]` (7 rows), `[兵]` (2), and `[高]` (1). All tokens match their official trainer-affiliation fields, all ten card/detail comparison names match exactly, and wrong-identity/collision counts are zero. The narrow allowlist preserves raw card names and does not change `P2_HORSE_IDENTITY_V1`. R4 promoted the formerly blocked Funabashi 10R and continued through 2026-08-16, then stopped independently at Ohi 2026-08-17 R8 on an unestablished raw `result_status` semantic.
- `P2-M12B-R10 Official Result-Status Vocabulary Semantic Recovery` — completed; all 204 bounded official result pages expose only numeric finish, `同着`, `出走取消`, `競走中止`, and `競走除外`, each with an explicit frozen M07 mapping. Ohi 2026-08-17 8R horse 10's exact `同着` display is a shared official rank 2, proven by 385 historical same-margin rows and three FS04 replay fixtures (zero mismatch). R4-A resumed to 204/204 races and 2,130 runners with `quick_check=ok` and clean FK. The next distinct hard gate is shadow-cutoff parity: the live delta is not yet wired into the actual V1/Class/Speed/Pace state builders, so P7--P11 remain blocked and no live inference occurred.
- `P2-M12B-R11 Base+Delta Online State Overlay Recovery` — stopped at reuse/interface audit. The existing date-only `P2HistoricalAsOfView` is not consumed by any online builder. The append-only delta also lacks the complete normalized race/runner/horse entities and frozen M02/M04/M05 observation inputs that those builders use. A four-way ad-hoc reconstruction would violate the required one-shared-overlay rule, so no false parity, model work, inference, or prediction operation was attempted.
- `P2_RESULT_COMPLETENESS_STATE_CONTRACT_V1` and its idempotency hotfix — completed; result-source, model-history-readiness, and WIN/WIDE/TRIO payout-readiness are independent persisted axes.  `DAY_COMPLETE_HISTORY_PENDING` preserves scientific completion while reporting recoverable history readiness.
- `P2_OHI_20260902_POST_LIVE_AUDIT_023` — completed; the first full Ohi live-day pre-race evidence, official post-race evidence, and actual manual-purchase accounting were reconciled without changing any decision contract.

## Blocked items
- No P2-A01 blocker identified by P2-A00.
- A confirmed historical actual pre-race market-snapshot collector remains unavailable for later market-baseline work.
- Historical actual pre-race snapshots remain unavailable for historical Market development.  Separately, the 2026-09-02 Ohi live day has seven immutable validated `T15_STANDARD` prospective references; they are operational evidence, not historical backfill.
- The retained 2026-08-19 Kawasaki stabilization fixture remains `LATE_AFTER_DECISION` and does not increment the CURRENT stabilization gate.  This limitation does not negate the separately retained 2026-09-02 Ohi T15 references.
- The official P2_CURRENT card adapter is implemented from the retained stabilization fixture; it remains source-quality/stabilization-only and does not activate a decision-time model feature.
- Historical aggregate work must exclude the 128 `nankan_history.sqlite.races` rows dated after the declared raw-corpus cutoff (`2026-07-31`) regardless of any future provenance recovery. Their raw-byte provenance remains unresolved; the date/race-key isolation audit passed.

## Next job
`P2-RACE-DAY-PERFORMANCE-DESIGN` — User + ChatGPT first perform a
source-direct critical-path scan, use Hotfix-019 timing evidence, define LIVE
budgets/DAG/safe concurrency and caching boundaries, preserve numerical and
scientific identity, and specify failure behavior.  Only then may the frozen
design be divided into narrow Codex implementation tasks.  Focus includes
T15-to-Main and T15-to-Ohi-WIDE actionability, WIDE/TRIO CPU contention,
child scheduling/polling, repeated loading/query/computation, SQLite/I/O, and
large-field scaling.  The known 11R incident was 16 runners / 120 WIDE pairs,
about 669 seconds T15-to-Experimental decision, and about 260 seconds to
post; it is evidence to inspect, not an optimization hypothesis.

## Known data limitations
- Monthly official odds from 2026-03 through 2026-07 are `MARKET_TIME_UNKNOWN` and development references only.
- NAR lacks confirmed model-ready runner-level first-3F and normalized runner-level corner positions.
- Canonical class hierarchy/strength/delta remain Phase 2 work.
- Keibabook market and prediction fields remain excluded; external data is a separate experiment.
- Historical same-day bias is primary-prohibited pending a timestamped availability contract.
- `horses.last_seen_date` is forward-looking global entity metadata and is prohibited from historical as-of feature construction.
- Raw NAR race-type labels are inventoried but are not yet an approved official/non-standard event mapping.
- The retained V1 `horse_key` construction is opaque in available V1 tools and is not extended to all-venue raw data.
- `p2_history_context.sqlite` stores completed historical outcomes for prior-race context only. Current target-race outcome joins, same-day history, and `target_horses.last_nankan_date_metadata` feature use are prohibited.
- Historical race-pre `program_points`, class-boundary positions, and program-point boundary deltas are unavailable and must not be fabricated from prizes or current values.
- P2-M03A ratings are an internal historical-result protocol only. They do not approve `P2_XVENUE` model use, transfer seeding, same-day updates, or a statistical confidence interval.
- P2-M03B `rating_information_depth` is deterministic `log1p(prior_valid_pairs)`, not posterior variance or a confidence interval. Other-flat counts are context metadata only and are not approved Main features.
- P2-M04A going adjustment is `REJECTED_NOT_SUPPORTED` after its registered 2025 validation failure. The amended course-only standard is provisional development-only and requires new prospective development data before any confirmatory claim.
- P2-M04B P2_SPD history uses only finite-z non-exchange South Kanto observations strictly before the target date. Other-flat history, same-day/current-race observations, going/class adjustment, and quality weighting are prohibited.
- P2-M05A runner first-3F remains unavailable from NAR and external-only. NAR runner corners are `NOT_MODEL_READY`; M05B may use last-3F and exact race-level pace only.
- P2-M05B `P2_PACE_MAIN_V1` is `PROVISIONAL_DEVELOPMENT_FEATURE`. It represents runner closing relative performance and prior race pace environments, not early speed, pace pressure, or running-style labels. Main history excludes exchange and other-flat observations and uses strictly earlier calendar dates only.
- `P2-INC-001` records one unregistered March-to-April inner-validation two-tree probe before M09. It is not a formal configuration evaluation, is excluded from selection, and requires persistent disclosure in resumed M09 evidence wording.

## Primary decision time
`T-15 ENGINEERING_CANDIDATE` — `NOT_FROZEN`.

## Snapshot collector
`LIVE_FRESHNESS_TEST_COLLECTOR_EXISTS; NOT_CONFIRMED_OPERATIONAL`.

## Manual wagering
`USER_EXECUTES_MANUALLY`.
