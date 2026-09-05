# P2-M12B-RESUME — Online Shadow Pipeline

## Inputs

- R1 exact official current identities and saved `PREDECISION_VALID` T15 raw.
- Frozen FS04 feature registry, M10 H2-C04/WF3 boost round, M08A Market normalization, and M12A ledger.

## Outputs

- Strict-as-of online FS04 materializer, deterministic `DEV-LIVE-V1`, immutable prediction snapshots, one-command shadow bundle, and auditable replay/E2E fixtures.

## Invariants

- Only historical `race_date < target_date`; no result DB read in pre-race path.
- Exact active-roster reconciliation; no name-only identity or silent intersection.
- FS04 has exactly 178 frozen tree fields. P2_CURRENT and Keibabook are context-only.
- No new search, H2-C05 evaluation, thresholding, payout, or ROI.

## Checkpoints

1. `P1_ONLINE_V1_119` — exact historical V1 materialization parity.
2. `P2_ONLINE_CLASS_24` — class rule/empirical/uncertainty parity.
3. `P3_ONLINE_SPEED_15` — speed parity.
4. `P4_ONLINE_PACE_20` — pace parity.
5. `P5_FS04_178_HISTORICAL_PARITY` — exact composition and roster-relative parity.
6. `P6_DEV_LIVE_V1_MODEL` — fixed-19-round deterministic shadow model.
7. `P7_LIVE_INFERENCE_COMMAND` — pending `P2-M12B-R4` `LIVE_HISTORY_FRESHNESS_GATE`: append-only official live-history delta must supply all finalized Nankan history strictly before the target date.
8. `P8_ANALYSIS_BUNDLE` — atomic source-separated bundle and decision template.
9. `P9_PREDICTION_FREEZE` — immutable ledger transaction and idempotency.
10. `P10_HIDDEN_RESULT_E2E` — existing M12A decision/result/reconciliation path.
11. `P11_2026-08-20_ENGINEERING_REPLAY` — post-event-only, no-result-access replay.

Each checkpoint is atomic. A failed checkpoint terminates the job without
promoting a later-stage artifact.

## P2-M12B-R4 recovery plan

### Inputs

- Immutable `db/p2_history_context.sqlite` through 2026-07-31.
- Official Nankankeiba calendar, race-card, result, and official horse-detail
  sources only.
- Existing P1--P6 PASS artifacts; R1 identity and R2 direction contracts.

### Outputs

- Append-only `db/p2_live_history_delta.sqlite` and one shared strict-as-of
  history view.
- Official-source/provenance, transaction, idempotency, same-day, cutoff
  parity, state-update, and freshness-gate audits.
- Only after the R4 gate passes: P7--P11 completion artifacts.

### Invariants

- The base context and V1 references remain unmodified.
- Delta accepts final official Nankan races only after 2026-07-31; no
  base/delta overlap or silent overwrite.
- Online history is always `race_date < target_date`; result DB is never read
  by pre-race inference.
- R4 performs no model search, performance computation, or ROI analysis.

### Phase gate

1. Official-source and required-field audit.
2. Delta schema, daily discovery, transaction and idempotency implementation.
3. 2026-08-01--2026-08-20 all-Nankan official finalized backfill.
4. Shared strict-as-of overlay, same-day boundary, simulated cutoff parity and
   block-state-update tests.
5. `LIVE_HISTORY_FRESHNESS_GATE = PASS`, then resume P7--P11 sequentially.

### P2-M12B-R5 narrow recovery

- Preserve the R4 card/detail-name conflict as an incident artifact.
- For an official horse-detail display title only, remove one terminal exact
  `（抹消）` annotation solely to create `horse_detail_name_identity`.
- Keep the raw title, the card name, and the birth date unchanged; any other
  annotation remains a source-semantic block.
- Resume R4-A only after the official sample audit has no remaining conflict.

### P2-M12B-R6 NONSTARTER semantic audit

- Before finding any additional identity source, derive the frozen historical
  effect of `NONSTARTER` rows from the actual V1/Class/Speed/Pace predicates.
- Compare normal history with a view that excludes only NONSTARTER runner
  updates while retaining race metadata; no feature/model formula is changed.
- Only if full FS04 state is unchanged, record an auditable
  `race_nonstarter_events` row without a synthetic horse identity and resume
  R4-A from the preserved 2026-08-07 Urawa 2R failure.
- **Result: BLOCKED.** The frozen M03B `pending_previous` state records every
  pre-row, so removing a NONSTARTER changes later Class prior-race/transition
  fields. No nonstarter-event separation was promoted; a future official
  identity-source recovery is required.

### P2-M12B-R7 official pedigree crosswalk audit

- Audit only static `horse_name_exact + sire + dam + damsire` fields on the
  official detailed card and exact canonical-master tuples.
- I1/I2 direct official identity remains first choice.  The proposed I2
  fallback is permitted only for a complete current-card tuple with precisely
  one master `horse_identity_key`; missing components, zero candidates, or
  collisions block.
- Validate with a 100-runner hidden-detail-ID simulation before modifying the
  R4 ingestion path.  No result-page, Keibabook, name-only, or fuzzy route is
  in scope.
- **Result: PASS.** 43,544 canonical tuples are complete with zero collision
  groups; the 100-runner hidden-detail-ID simulation has zero wrong identities.
  The preserved Urawa 2R nonstarter #5 resolves uniquely by the approved tuple.
  R4-A resumes from that preserved failure; R1--R6 and P1--P6 are not rerun.

### R4-A resume result after R7

- Urawa 2R now commits with its exact canonical identity; 2026-08-07 races
  1--5 are promoted (five races, 46 runner rows).
- R4-A stops at Urawa 6R, separately from identity: the official final result
  has nine numeric finish positions plus one `競走中止` runner while official
  `field_size` is ten.  The existing parser treats the latter as an unresolved
  roster and cannot safely determine its frozen history-update semantics.
- This is a new required-field/status-semantic block.  Do not continue to R4-B
  or P7--P11 until an explicit audit establishes the frozen treatment of this
  official `競走中止` status.

### P2-M12B-R8 starter-no-valid-finish recovery

- Inputs: frozen M07 outcome registry and historical `競走中止` runner rows.
- Required proof: `競走中止` must map to the pre-existing
  `STARTER_NO_VALID_FINISH` semantics and retain the exact historical raw
  representation (`RAW_FINISH_STATUS_MISSING`, NULL finish/time/last3F,
  preserved body weight and raw margin).
- No arbitrary finish rank, nonstarter conversion, runner deletion, or
  feature/rating formula change is permitted.
- Only after historical precedence, block-predicate audit, parser regression,
  and later-start feature parity pass may Urawa 6R be committed and R4-A
  resume.

### P2-M12B-R8 result (2026-08-21)

- **PASS:** raw `競走中止` is already frozen as
  `STARTER_NO_VALID_FINISH`. Historical precedent contains 921 runner rows in
  870 races, all with NULL numeric finish, finish time, and last-3F.
- The live normalized row exactly preserves that form. V1 and Class retain
  only their existing starter/prior-race state effects; Speed and Pace do not
  create a runner performance observation. Five later-start fixtures (65
  runner rows) pass FS04-178 parity with zero mismatches and maximum numeric
  difference `5.000444502911705e-13`.
- Urawa 2026-08-07 R6 now commits with the stopped starter intact. This did
  not change any feature, outcome, rating, model, or search semantics.
- R4-A then resumed from its preserved checkpoint and later stopped at a new,
  separate official card/detail name conflict for 2026-08-05 Funabashi R10:
  the card uses an unapproved `[J]` designation while the official detail page
  uses the bare canonical name. Do not normalize it without a separately
  approved source-semantic recovery.

### P2-M12B-R9 official runner affiliation-prefix recovery

- **PASS:** the fixed 204-card R4 audit found only `[J]` (7 rows), `[兵]` (2),
  and `[高]` (1). Each exact leading token agrees with an official trainer
  affiliation (`JRA`, `兵庫`, `高知`) and all ten card/detail comparison names
  match, with zero wrong identity and zero collision.
- The allowlist is exact and observed-only. Raw card name, prefix, and card
  comparison name are stored separately. R5 `（抹消）` handling remains a
  separate detail-page layer; canonical identity is unchanged.
- The former Funabashi 2026-08-05 R10 block committed. R4 progressed through
  2026-08-16 and stopped independently at Ohi 2026-08-17 R8 on an unknown
  official `result_status` semantic. Do not continue to R4-B/P7--P11.

### P2-M12B-R10 official result-status vocabulary recovery

- **PASS:** all 204 official final result pages were boundedly audited (165
  retained result raws and 39 R10 audit captures). Only numeric finish,
  exact finish display `同着`, and the existing M07 missing-finish margins
  `出走取消`, `競走中止`, and `競走除外` occurred. No observed token is
  unresolved.
- Ohi 2026-08-17 R8 #10's raw `同着` is explicitly represented as the
  immediately preceding shared official rank only when the raw margin is also
  `同着` and the finish time is exactly equal. Historical context has 385
  matching rows; three later-race FS04 fixtures (38 rows) are exact with a
  maximum numeric difference of `4.875266856885219e-13`.
- R4-A resumed through all 204 races / 2,130 runners, with `quick_check=ok`
  and no foreign-key violations. The new distinct hard gate is not source
  collection: the shared date view does not yet provide base+delta runner
  state to the actual V1/Class/Speed/Pace online builders. Do not advance to
  P7--P11 until simulated shadow-cutoff FS04 parity establishes that overlay.

### P2-M12B-R11 base+delta overlay recovery

- **BLOCKED:** interface audit establishes that `P2HistoricalAsOfView` unions
  only race dates and is not used by the V1/Class/Speed/Pace builders. The
  delta's schema is insufficient for those frozen input paths (normalized
  race/runner/horse state plus M02 class and M04/M05 observation inputs).
- Do not create a parity-only substitute or four separate overlay mechanisms.
  A single read-only normalized base+delta provider must be defined and
  validated before the shadow-cutoff FS04 hard gate can be run. No P7--P11,
  model retraining, prediction, result access, performance, or ROI action was
  performed in R11.
