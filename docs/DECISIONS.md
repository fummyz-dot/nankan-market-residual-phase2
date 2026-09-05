# Phase 2 Decisions

- `D-P2-001` — Phase 2 is separate from V1.
- `D-P2-002` — V1 reference assets are immutable.
- `D-P2-003` — 2026-07 and earlier are development only.
- `D-P2-004` — Final operation is manual purchase.
- `D-P2-005` — Target recommendation timing is 10–20 minutes before post.
- `D-P2-006` — T-15 is an engineering candidate, not frozen.
- `D-P2-007` — Keibabook is collected from the start but evaluated separately.
- `D-P2-008` — Git is not currently required.
- `D-P2-009` — SHA-256 manifests replace Git provenance for now.
- `D-P2-010` — P2-A01 uses the declared raw-corpus cutoff (2026-07-31); later history DB rows are excluded pending provenance resolution.
- `D-P2-011` — P2_CLASS_RULE is non-ordinal and draft-only; empirical class strength remains unimplemented.
- `D-P2-012` — Historical same-day bias is primary-prohibited until a timestamped availability contract is approved.
- `D-P2-013` — The 128 history rows dated after 2026-07-31 remain excluded from Phase 2 historical-development aggregates. Their raw-byte provenance is unresolved, while date/race-key isolation passed; `horses.last_seen_date` is prohibited in historical as-of construction.
- `D-P2-014` — Prospective capture is source-agnostic until an authorized live raw sample is archived and reviewed. T-15 remains `ENGINEERING_CANDIDATE`, and Keibabook is stored only in `P2X_O`/`P2X_S`, not Phase 2 Main.
- `D-P2-015` — Nankan official historical pages are parser fixtures only. Their parsed odds are `HISTORICAL_FIXTURE_ONLY`; observed odds URL patterns are not a live URL-generation contract.
- `D-P2-016` — The live freshness probe is foreground-only and records T-20/T-15/T-10/T-5 as `LIVE_FRESHNESS_TEST`; its T-15 record is a non-frozen engineering candidate, not a model/prediction authorization.
- `D-P2-017` — A race analysis bundle may use only an explicit retained `PRIMARY_CANDIDATE` market capture, never a latest snapshot. P2 Main, Keibabook objective (`P2X_O`), and training (`P2X_S`) remain structurally separate; the bundle contains no model or ticket output.
- `D-P2-018` — For the audited 2020-01–2026-07 retained raw NAR corpus, exact raw `馬名 + 生年月日` is the approved identity for historical completeness joins between South Kanto target horses and other flat NAR history only. Ban'ei is excluded, no fuzzy or name-only join is allowed, same-day history remains prohibited, and `P2_XVENUE` remains unapproved for model use.
- `D-P2-019` — `db/p2_history_context.sqlite` is the provenance-linked, raw-semantic-preserving historical context store for the audited flat NAR corpus. It is not a model dataset: South Kanto remains the only target/loss/evaluation universe, Ban'ei remains excluded, `P2_XVENUE` remains unapproved, and same-day/current-target outcome use remains prohibited.
- `D-P2-020` — P2-M02 applies official-source-backed, date/age-scoped Nankan class rules only to `NANKAN_TARGET` races. A1–C3 ordinal is institutional order only; mixed classes retain their full code set; legacy thresholds and historical as-of program points remain unresolved/not available; `P2_XVENUE` remains unapproved.
- `D-P2-021` — P2-M03A freezes `P2_CLASS_EMPIRICAL_MAIN_V1` as the single online pairwise Bradley–Terry family with `R3` (`K=1.00`), selected only from 2021–2024 race-equal pairwise log loss. Updates use South Kanto safe completed results in calendar-date blocks, exclude exchange/bare-exchange races, and never seed from other-flat history; 2025 validation and 2026 diagnostic cannot alter the configuration.
- `D-P2-022` — P2-M03B freezes the strict-as-of empirical class feature construction: rated means exclude cold-start origin scores; context and previous-race state use only strictly prior dates; `rating_information_depth=log1p(prior_valid_pairs)` is information metadata, not a CI; other-flat counts are `CONTEXT_METADATA_ONLY`; and RuleOnly/RulePlusEmpirical remain the sole class ablations.
- `D-P2-023` — P2-M04A selected S3 solely from the registered 2021–2024 clock MAE, then failed its one-time 2025 comparison against the fixed course-only reference. The status is `SPEED_STANDARD_WEAK_REVIEW_REQUIRED`; no extra lookback, family, class adjustment, or Market-based change is authorized.
- `D-P2-024` — P2-AMEND-001 records `P2_SPEED_GOING_ADJUSTMENT_V1 = REJECTED_NOT_SUPPORTED` and freezes the pre-specified `COURSE_ONLY_ALL_HISTORY` reference as separately versioned `P2_SPEED_STANDARD_MAIN_V1`, `PROVISIONAL_DEVELOPMENT_FEATURE`. M04A's S3 artifact is retained unchanged; no new speed search is authorized, and already-seen 2025/2026-07 data cannot provide confirmation of the amended standard.
- `D-P2-025` — P2-M04B freezes `P2_SPEED_FEATURE_LIST_V1`: strict-prior, finite-z, non-exchange South Kanto speed history only, with the registered last/recent/exact-course aggregates and count metadata. P2_SPD remains `PROVISIONAL_DEVELOPMENT_FEATURE`; no going/class adjustment, decay, distance-similarity, other-flat, or Market input is approved.
- `D-P2-026` — P2-M05A promotes safe NAR runner last-3F and deterministic exact race-lap/pace observations to the M05B history-build candidate set. Race first-3F requires an exact segment boundary and no interpolation. NAR runner corners remain `NOT_MODEL_READY`; Keibabook runner first-3F/corner fields remain external-only P2X-O and QA-only here.
- `D-P2-027` — P2-M05B freezes `P2_PACE_MAIN_V1` as strict-prior, non-exchange South Kanto history of only runner last-3F-relative observations and exact race pace-balance environments. The registered closing and pace-exposure feature lists are `PROVISIONAL_DEVELOPMENT_FEATURE`; they do not create runner early-speed, corner, running-style, Keibabook, other-flat, Speed/Class-adjusted, or Market-derived features.
- `D-P2-028` — P2-M06 freezes the exact 119-column V1 active semantic port, the label-free 250,093-row historical-development matrix, and only FS00–FS04 as pre-performance P2 Main candidate sets. P2_SPD/P2_PACE retain provisional status; eligibility remains metadata only; historical roster is not asserted equivalent to a T-15 active roster.
- `D-P2-029` — P2-M07 freezes `P2_PRIMARY_RACE_UNIVERSE_V1` as a development-only pre-race semantic universe: explicit JRA exchange/newcomer/C3 are excluded; safely explicit A1–C2 and high-level/open contexts are eligible; unprovable floor contexts are secondary-only. Separate outcome semantics preserve all runner rows and use `WIN_SOFT_TIE_TARGET_V1`; FS00–FS04 and the future market-offset race-softmax form are fixed without accessing Market or fitting a model.
- `D-P2-030` — P2-M08A freezes `RAW_NORMALIZED_WIN_MARKET_V1` and the all-venue `POWER_GAMMA_V1` calibration method with capture-time roster normalization, no clipping/imputation, race-equal soft-target loss, and deterministic root solving. Historical `MARKET_TIME_UNKNOWN` is diagnostic-only; no actual T-15 gamma, residual model, payout, ROI, or stabilization outcome use is authorized.
- `D-P2-031` — P2-M08B freezes `LIGHTGBM_GBDT` (CPU LightGBM 4.7.0) as the sole H1 backend; `NATIVE_INIT_SCORE_V1` Market offset; exact race-softmax `p-y` gradient; `DIAGONAL_SOFTMAX_HESSIAN_APPROX_V1`; FS00-only fold-safe native-categorical preprocessing; three pooled nested walk-forward folds; and exactly six shallow/L2 legacy residual configurations. M08B consumes zero performance configurations. Historical Market remains development-reference-only, and T-15/Primary gamma remain unfrozen.
- `D-P2-032` — Before H1 performance evaluation, `ZERO_TREE_BASELINE_EARLY_STOPPING_CLARIFICATION_V1` makes frozen `f=0` iteration 0 an inner early-stopping candidate. Ties within `1e-10` select the smaller iteration; an iteration-0 winner writes no tree and must reproduce calibrated Market exactly. No feature, parameter, configuration, or search-budget axis is added.
- `D-P2-032` — Before first H1 performance evaluation, `ZERO_TREE_BASELINE_EARLY_STOPPING_CLARIFICATION_V1` makes frozen `f=0` iteration 0 an inner-validation early-stopping candidate. Candidate-loss ties within `1e-10` select the smaller iteration; an iteration-0 winner produces no tree artifact and must reproduce calibrated Market exactly. This is not a new configuration, parameter, feature, or search-budget axis.
## D-P2-033 — P2-M09 pre-performance protocol incident (2026-08-19)

Before the registered M09 performance evaluation, an engineering check
accidentally used the real historical FS00 frame and calculated an inner
validation loss for a two-tree run.  This is outside the frozen
six-configuration, three-outer-fold procedure.  It is recorded at
`audit/data/p2_m09/PRE_PERFORMANCE_PROTOCOL_INCIDENT.md`.

The formal M09 evaluation, selection, bootstrap, and search-budget consumption
are stopped pending an explicit recovery decision.  No result-changing
parameter, fold, feature, gamma, or backend change was made in response.

## D-P2-034 — P2-M09R recovery authorization (2026-08-19)

Project-owner recovery decision: `P2-INC-001` does not require new unseen
development evidence if the M09R forensic integrity audit passes.  The one
known observation is bounded to a March-to-April inner-validation two-tree
probe.  May, June, and July outer-validation data must remain untouched; formal
H1 search budget remains `0/6`, with a separate permanent incidental-peek count
of `1`.  No post-peek adaptation to configuration, features, folds, gamma,
backend, or model mathematics is authorized.  M09 may resume unchanged only
through the explicit formal-execution guard and with the evidence label
`DEVELOPMENT_EVALUATION_WITH_RECORDED_PROTOCOL_INCIDENT`.

## D-P2-035 — P2-M09 formal H1 result (2026-08-19)

The first formal registered six-configuration H1 run after `P2-INC-001`
completed with the recovery protocol intact. All six configurations were
evaluated once across WF1–WF3; formal H1 budget is exhausted at `6/6`, separate
from the permanent incidental-peek count `1`. Frozen pooled selection chose
`H1-C06` with candidate-minus-calibrated-Market race-equal delta
`+0.0008409107783122695`, so the development-only decision is
`H1_HISTORICAL_NO_SIGNAL`. This is historical `MARKET_TIME_UNKNOWN` evidence,
not T-15 evidence or a probability-edge confirmation. No H1 rescue search,
parameter change, feature action, calibration, clipping, or rerun is
authorized; independent H2 development remains allowed.

## D-P2-036 — P2-M10 formal H2 NAR-core historical result (2026-08-19)

The four preregistered H2 NAR candidates completed under the frozen H1-C06
backend, M09 folds, and exact M09 fold-gamma values. FS04 was the
pre-designated NAR-core candidate and was not selected from C01–C04 by
performance. Its pooled race-equal candidate-minus-calibrated-Market loss was
`+0.0023155723053499225`; the development-only status is
`H2_NAR_CORE_HISTORICAL_NO_SIGNAL`. The H2 search budget is `4/6`; H2-C05
remains a separately prospective P2_CURRENT candidate and H2-C06 remains
unallocated. This result is `HISTORICAL_MARKET_TIME_UNKNOWN`,
`DEVELOPMENT_REFERENCE_ONLY`, and `H2_EVIDENCE_NOT_FRESH_HOLDOUT`; it neither
confirms a probability edge nor changes H1, T-15, or actual Primary gamma.

## D-P2-037 — P2-M11A P2_CURRENT prospective foundation (2026-08-19)

CUR01–CUR06 are frozen as source-quality candidates before any outcome or
feature-performance work. The official-only day collector records T20/T15/T10/
T05 raw current-card captures, with a ten-second pre-mark collection lead,
atomic no-backfill checkpoints, and no child workers. Existing Kawasaki 5R
fixture parity is retained, but its nominal marks were captured several seconds
late and are not availability evidence for T15 activation. H2-C05 remains
`REGISTERED_NOT_EVALUATED`; H2-C06 remains `UNALLOCATED`; stabilization and
T15 are not frozen. Any candidate activation is based only on timestamp,
semantic, roster, duplicate, and coverage gates—not performance.

## D-P2-038 — P2-M11A-R stabilization gate and T15 capture timing amendment (2026-08-19)

Before any stabilization outcome use or P2_CURRENT performance evaluation,
`P2_STABILIZATION_GATE_V1` is superseded by retained `P2_STABILIZATION_GATE_V2`.
The intentionally shortened engineering minimum is 14 calendar days, 80
distinct Primary-eligible races with predecision-valid T15 capture, all four
venues with at least one meeting, and at least 10 distinct Primary-eligible
predecision-valid T15 races per venue. The existing coverage, provenance, parser, join, duplicate, and clock
quality gates remain. This is an operational/data-quality amendment, not
model-performance tuning.

For T15, decision time is `scheduled_post_time - 15 minutes`; availability
proof is only a capture from `decision_time - 60 seconds` through decision time
inclusive. The collector begins the request 30 seconds before T15 and may make
one retry only before decision time. Earlier captures are `STALE_FOR_T15` and
later captures are retained as `LATE_AFTER_DECISION`, but neither can activate
P2_CURRENT or increase valid-T15 coverage. The retained 2026-08-19 Kawasaki 5R
fixture is unchanged and remains parser parity evidence only: its approximately
five-seconds-late T15 capture is not predecision availability proof. H2-C05
remains unevaluated, H2-C06 unallocated, no outcome was accessed, and T15 is
not frozen.

## D-P2-039 — P2-M11A-S collector observability and fail-fast safety (2026-08-20)

The foreground prospective collector now provides a non-capturing official
preflight, atomic per-race and daily live status, waiting heartbeats, and
machine-readable lifecycle/failure events. A separate status command is
read-only and does not stop or restart collection. Day-fatal discovery/storage
failures are distinct from race-scoped capture/parser/late/roster failures, so
later races continue after a safe race-scoped failure. These operational
observability changes access no outcome, performance, payout, or ROI data.

## D-P2-040 — P2-OPS-001 live collector FK / failure-state hotfix (2026-08-20)

`P2-OPS-001` records the race-scoped `2026-08-20 川崎01R T20` operational
failure. The raw archive UUID was not passed to `record_capture`, which created
a distinct `source_captures.capture_id`; dependent current-info/market rows then
correctly failed their foreign-key constraint. Foreign keys remain enabled. The
collector now registers the archive UUID before dependent inserts in one
transaction. Failed captures use `.failed.json`, never update `last_completed`,
and emit `CAPTURE_FAILED`; only successful captures produce `.complete.json` or
`CAPTURE_COMPLETE`. The original failed artifact remains immutable evidence and
is excluded from success/resume promotion. This is operational only: no outcome,
performance, payout, or ROI data was accessed.

## D-P2-041 — Compact human collector status (2026-08-20)

The read-only collection-status command now defaults to an at-most-20-line
human summary with explicit `HEALTHY`/`WARNING`/`ERROR`, collector freshness,
last successful/attempted capture, next capture, T15 validity counts, and only
active or historical operational warnings. `--verbose` adds the per-race view;
`--json` preserves raw structured output. Future `WAITING` is normal. Known
`P2-OPS-001` remains visible as a historical race-scoped warning and is distinct
from a fatal fault. The command exits `0/1/2` for healthy/warning/error. No
collector capture, schema, timing, outcome, or performance behavior changed.

## D-P2-042 — P2-M12A isolated live development ledger (2026-08-20)

Live development decisions, official runner results, official WIN/WIDE/TRIO
payouts, and deterministic reconciliation are physically isolated in
`db/live_development.sqlite`. A decision can be evaluation-eligible only if it
was frozen strictly before scheduled post time; frozen content is immutable and
post-result decision creation cannot change eligibility. Official raw captures
are provenance-complete and parsed payout amounts retain
`PAYOUT_UNIT_UNRESOLVED` when the official page provides no explicit unit, so
P/L remains prohibited.

The official 2026-08-20 Kawasaki 6R–11R smoke collection completed twice
without duplicate logical rows. Because no pre-race frozen model decision
exists, all six reconciliation records are permanently `NO_PRE_RACE_DECISION`.
This was a collector/reconciliation smoke test only: model inference,
probability performance, payout use for ROI, and retrospective evaluation did
not occur. The prospective collector and `market_snapshot.sqlite` schema were
not modified.

## D-P2-043 — P2-M12B exact current horse-identity stop (2026-08-20)

M12B stopped before any DEV-LIVE-V1 training, inference, prediction freeze, or
performance observation. The approved current official snapshot has runner
number, body weight/change, and jockey, while the Keibabook context has a horse
name/ID but no birth date. Neither establishes the exact
`P2_HORSE_IDENTITY_V1 = horse_name + birth_date` composite. Name-only matching
to historical context is prohibited even if a current lookup happens to be
unique. The project requires an official current birth-date field or a
separately audited immutable official-ID crosswalk before online V1/Class/Speed/
Pace materialization can be implemented.

## D-P2-044 — P2-M12B-R1 official identity-source recovery (2026-08-20)

The stop is resolved without weakening `P2_HORSE_IDENTITY_V1`. Every saved
T15 predecision current card for Kawasaki 6R–11R contains an official
`/uma_info/<id>.do` link. I2 obtains the full detail date from that link and
validates card horse name, detail horse name, and short-date representation
before exact `horse_name + birth_date` history matching. The result is 69 exact
matches and one genuine cold start across 70 runners, with no unresolved
identity or collision. Result data and Keibabook did not participate. M12B may
resume from its online-feature step; historical conclusions remain unchanged.

## D-P2-045 — P2-M12B current target direction hard stop (2026-08-20)

R1 resolves only exact horse identity. The saved official T15 cards for
Kawasaki 6R–11R expose `ダ...m（外）`, not the frozen V1 categorical direction
`左`/`右`. Historical Kawasaki rows use `左`, but deriving that value from venue
or the layout token would be a new unverified mapping. Exact FS04 parity is
therefore blocked before any online model operation. This is a source-semantic
stop, not a performance result or protocol amendment.

## D-P2-046 — P2-M12B-R2 official course-direction source contract (2026-08-20)

D-P2-045 is superseded only for the source-semantic direction gap, not erased.
`P2_OFFICIAL_COURSE_DIRECTION_V1` now uses raw-archived official course pages
as a provenance-complete course-definition source.  Resolution is D1 explicit
official pre-race direction, then D2 approved official static mapping, then D3
hard block; D1/D2 disagreement is `BLOCK_SOURCE_CONFLICT`.  `外`/`内` layout is
not a direction source. Kawasaki, Funabashi, and Urawa are official fixed-left;
Ohi is limited to the specifically approved distance allow-list with no default
for unknown distances. Historical direction values were used only for a
zero-mismatch QA audit. This operational/source-semantic recovery accessed no
model, result, payout, performance, or ROI data and does not change T15,
gamma, H1/H2, or probability-edge conclusions.

## D-P2-047 — P2-M12B-RESUME2 online class parity stop (2026-08-20)

P1's result-free target adapter passed exact V1 parity. P2 did not: a
multi-fixture class adapter correctly withheld target outcomes, but consequently
withheld an earlier fixture's historical rating update from later fixture
dates. It produced 131 parity mismatches and is not promotable. This is a
feature-materialization implementation stop, not an evidence/performance
observation. No tolerance relaxation, parameter/model/search change, result
access, or later-stage continuation is authorized without a recovery decision.

## D-P2-048 — P2-M12B-R3 Class replay recovery and live-history gate (2026-08-20)

`D-P2-047` is preserved as an implementation incident and is resolved only by
the frozen M03 date-block replay behavior: a historical fake-live fixture is
emitted from state through the preceding calendar date, then its actual
historical update remains available to all later fixture dates. The recovered
Class output has 0 mismatches across the prior failed 55-row fixture set and
the sequential, independent single-target, same-day, and input-order checks
pass. No Class formula, `K=1.00`, feature definition, tolerance, update
universe, model/search protocol, or evidence was changed.

P3/P4/P5 parity and fixed-horizon `DEV-LIVE-V1` construction subsequently
passed. M12B is nevertheless stopped at P7: `p2_history_context.sqlite` is
bounded to 2026-07-31, so the online state cannot yet prove use of approved
post-July completed history for a later live target. This is a hard
`LIVE_HISTORY_FRESHNESS_GATE`, not permission to reuse a frozen July state.
No live prediction, decision, result-table inference read, performance, ROI,
or H2-C05 evaluation occurred after this finding.

## D-P2-049 — P2-M12B-R7 exact official pedigree crosswalk (2026-08-21)

`P2_HORSE_IDENTITY_V1 = exact horse_name + birth_date` is unchanged. When a
same-card official horse-detail link is absent, the sole fallback is the exact
static official detailed-card tuple `horse_name + sire + dam + damsire` to one
official-derived canonical master record. The direct detail route remains
priority I1. Missing fields, no canonical match, or more than one candidate
block; name-only, fuzzy, mutable personnel/sex fields, Keibabook, and result
sources remain prohibited. The canonical master has 43,544 complete tuples and
zero tuple-collision groups; a 100-runner direct-ID-hidden simulation has zero
wrong recoveries. This resolves Urawa 2026-08-07 R2 nonstarter #5 without
changing feature or Class semantics.

## D-P2-050 — R4 stop on official `競走中止` runner-status semantics (2026-08-21)

After R7, R4-A committed Urawa 2026-08-07 races 1–5 and then stopped at race
6. Its official final result has final field size ten, nine numeric finish
positions, and one raw `競走中止` runner. The live-history parser does not guess
whether this status is a frozen Class/V1/Speed/Pace history contributor.
This is distinct from the resolved nonstarter identity issue. A status-semantic
audit is required before promotion or later M12B stages; no model performance,
ROI, or prediction operation occurred.

## D-P2-051 — P2-M12B-R8 frozen `競走中止` recovery (2026-08-21)

The existing M07 status registry deterministically maps raw `競走中止` to
`STARTER_NO_VALID_FINISH`; no new racing semantic was added. Historical
South-Kanto precedent has 921 such runner rows in 870 races, all with NULL
numeric finish, finish time, and last-3F. The live-history adapter preserves
that representation and keeps the runner as a starter. Frozen builders show
that V1 and Class retain only their existing participation/prior-race effects,
while Speed and Pace create no runner performance observation. Five later
normal-start fixtures (65 rows) reproduce the complete FS04-178 M06 vectors
with zero mismatches and maximum numeric difference
`5.000444502911705e-13`. Urawa 2026-08-07 R6 commits atomically with its
stopped runner, NULL finish, and FK checks clean. No feature, protocol, model,
performance, ROI, or search-budget change occurred.

## D-P2-052 — R4 stop on unapproved official card `[J]` name annotation (2026-08-21)

After R8, append-only R4 backfill resumed and then stopped at 2026-08-05
Funabashi R10 (`2026080519050410`). Its official current-card horse names
contain the distinct `[J]` prefix for exchange-designated runners, while the
official horse-detail display name is bare canonical name (optionally with the
separately approved terminal `（抹消）` annotation). The R5 rule does not
authorize removal of `[J]`; stripping it would be a new identity-source
semantic. The preserved backfill evidence remains unchanged, and no further
R4/P7–P11 phase may proceed until a narrow approved recovery establishes its
official meaning and exact comparison rule.

## D-P2-053 — P2-M12B-R9 official runner affiliation prefixes (2026-08-21)

The fixed 204-card R4 audit observed exactly `[J]` (7 rows), `[兵]` (2), and
`[高]` (1). Each is an exact leading official card display annotation, is
consistent with the card's trainer affiliation (`JRA`, `兵庫`, and `高知`), and
removes to an exact official horse-detail comparison name. All ten detail
comparisons are exact; canonical-master collisions and wrong identities are
zero. These three—and only these three—are allowlisted. Raw card display,
token, and comparison name are retained separately; the R5 detail terminal
`（抹消）` rule remains independent. `P2_HORSE_IDENTITY_V1` and every
name-only/fuzzy prohibition are unchanged.

## D-P2-054 — R4 stop on official result-status semantic (2026-08-21)

After R9, R4 successfully promoted the preserved Funabashi 2026-08-05 R10
and continued through 2026-08-16. It stopped at Ohi 2026-08-17 R8 with
`BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_result_status_semantics`. This is a
new raw official outcome-status semantic, distinct from R8's audited
`競走中止` status. No substitution, runner removal, or feature-state inference
is authorized; R4/P7–P11 remain stopped pending a narrow frozen-semantics
audit.

## D-P2-055 — P2-M12B-R10 bounded official result-status vocabulary (2026-08-21)

R10 scanned the fixed 204-race R4 result scope (165 retained result raws plus
39 newly retained official result raws). The only observed nonnumeric finish
display is exact `同着`; the only missing-finish margin statuses are the
already-frozen `出走取消`, `競走中止`, and `競走除外`. `同着` is accepted only
through an explicit exact-display mapping requiring raw margin `同着`, the
immediately preceding positive numeric official rank, and identical official
finish time. It normalizes to existing `FINISHED` shared-rank semantics; it is
not an artificial rank or a general Japanese-status rule. Unknown displays
have no default and block. Historical context has 385 corresponding rows, and
three later fixture races reproduce FS04 exactly. The Ohi 8R transaction and
the complete 204-race R4-A backfill pass integrity checks. This changes no
feature, outcome, model, search, or performance semantics.

The delta's date accessor currently proves freshness only; it is not yet a
data source for the online V1/Class/Speed/Pace state builders. The required
base+delta shadow-cutoff parity is therefore a separate hard gate and P7--P11
remain prohibited.

## D-P2-056 — R11 stops before false base+delta overlay (2026-08-21)

R11's reuse audit found that the existing `P2HistoricalAsOfView` supplies only
race/date visibility and is consumed by no online feature builder. V1 directly
queries full normalized historical entities; Class consumes M03 replay and
class structures; Speed and Pace consume their respective frozen curated
observation streams. The live delta has neither the complete normalized raw
entities nor M02/M04/M05 inputs necessary to reproduce those paths. It is
therefore prohibited to claim a date-union overlay, omit delta state, or build
four separate substitutes. No shadow-cutoff parity, model operation, result DB
read, performance, or ROI calculation occurred. A single validated normalized
base+delta source is required before P7--P11.
