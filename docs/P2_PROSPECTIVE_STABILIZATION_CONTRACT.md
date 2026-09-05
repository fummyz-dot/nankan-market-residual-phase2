# P2 Prospective Stabilization Contract

The foreground command is `python3 -m src.operations.prospective_day_collector --date YYYY-MM-DD`.

It discovers explicit official race-card links from the Nankankeiba program page, validates each URL against its entry-page identity, and captures T20, T15, T10, and T05. For T15, the request starts 30 seconds before the nominal mark. The operational lead may be raised to 45 seconds for timing safety, never beyond the hard 60-second bound. Discovery or identity ambiguity produces `BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY`; third-party or manually enumerated race URLs are not substituted.

Completed marks are checkpointed and skipped on resume. A past missing mark is recorded as `MISSED`, never backfilled or relabelled. For T15, at most one bounded retry is allowed only while the decision time has not passed. A response completed after the decision time is retained as raw evidence but is `LATE_AFTER_DECISION`; it is never retried or relabelled as a valid T15 capture. The collector is one foreground process with atomic RUNNING/COMPLETE/FAILED markers, heartbeat, and no children. It accesses no outcomes, payouts, or model functions.

## T15-standard and pre-race recovery reference

`P2_PRE_RACE_CAPTURE_POLICY_V1` keeps `T15_STANDARD` as the sole scientific
reference: a retained `PREDECISION_VALID` T15 capture is always selected even
if a later T10/T05/RECOVERY capture exists.  Its bundle provenance has
`scientific_sample=true`.

If no valid T15 exists, a valid retained T20/T10/T05/`RECOVERY` capture set may
be selected as `PRE_RACE_FALLBACK` only while it is pre-post, roster-valid,
WIN-valid, and at most 900 seconds old when bundled.  It remains prospective
but carries `scientific_sample=false`, so it cannot silently enter the standard
T15 sample.  WIN and WIDE must come from the exact same retained CURRENT
capture set; incomplete WIDE is a WIDE-only partial condition and does not
invalidate a valid WIN reference.

When the collector resumes after T15, or `race-shadow` finds no valid reference,
the shared resolver may make a marked `RECOVERY` capture immediately only with
at least 120 seconds (inclusive) remaining to post.  A per-race file lock
rechecks storage after acquisition, preventing duplicate recovery requests.
Only transient source failures may retry, at the fixed 30-second interval and
at most three attempts.  Below 120 seconds no new capture is requested and the
normal outcome is `SHADOW_SKIPPED` / `TOO_LATE`, not a traceback.  `RECOVERY`
is never recorded as T15.

`P2_STABILIZATION_GATE_V1` is superseded, retained, and not silently overwritten. Active readiness is measured, not declared, by `P2_STABILIZATION_GATE_V2`: >=14 calendar days, >=80 Primary-eligible races with a `PREDECISION_VALID` T15 capture, every venue with >=1 meeting and >=10 distinct Primary-eligible races with a predecision-valid T15 capture, coverage >=97% overall and >=95% per venue, zero joins/duplicates, 100% raw provenance, zero fatal parser/schema drift, and absolute capture-offset p99 <30 seconds. The status command writes `reports/prospective/P2_STABILIZATION_STATUS.{json,md}`. No outcome-dependent extension or shortening is allowed.

For a nominal T15 decision time `D = scheduled_post_time - 15 minutes`, P2_CURRENT availability proof requires `D - 60 seconds <= captured_at <= D`. This is `PREDECISION_VALID`. Earlier captures are `STALE_FOR_T15`; later captures are `LATE_AFTER_DECISION`. Only `PREDECISION_VALID` captures enter T15 coverage, current-field source coverage, or eventual P2_CURRENT activation evidence. T15 remains an engineering candidate and is not frozen.

## Collector observability

`python3 -m src.operations.prospective_day_collector --date YYYY-MM-DD --preflight` performs official day discovery and schedule, output-directory, SQLite quick-check/table, duplicate-identity, timezone, checkpoint, and past-mark checks without waiting for or performing a capture. It atomically writes `preflight.json`.

The foreground collector atomically writes a race status immediately after every mark to `races/raceNN_status.json`, a daily `live_status.json`, a waiting heartbeat, and machine-readable events. A read-only second-terminal view is available through `python3 -m src.operations.prospective_collection_status --date YYYY-MM-DD`; it writes neither the SQLite store nor collector artifacts. Its default is a compact human health summary; `--verbose` adds per-race state and `--json` prints raw structured status. Exit codes are `0=HEALTHY`, `1=WARNING`, and `2=ERROR`. Future `WAITING` is normal, while known historical race-scoped incidents are shown separately from active/fatal faults. T15 statuses distinguish `WAITING`, `PREDECISION_VALID`, `LATE_AFTER_DECISION`, `STALE_FOR_T15`, `MISSED`, `CAPTURE_FAILED`, `PARSE_FAILED`, and `IDENTITY_FAILED`.

Official discovery/storage corruption is a `DAY_FATAL_FAILURE`; a single race network, parser, late-T15, or roster-reconciliation failure is `RACE_SCOPED_FAILURE` and does not prevent later races from being collected. These observability artifacts do not access outcomes, model performance, or ROI.

## P2_CURRENT component metrics V2

`src.operations.stabilization_status` reports P2_CURRENT components
independently.  CUR01/CUR02/CUR06 use committed prospective CURRENT snapshots;
failed historical raw is regression-only and never increases committed
coverage.  CUR04/CUR05 remain `NOT_IMPLEMENTED` until an approved parser
exists.

CUR03 stabilization reads only immutable
`p2_current_research_evidence_v2` / `P2_CURRENT_JOCKEY_CONTEXT_V2` artifacts.
V1 evidence is shown separately as `CURRENT_JOCKEY_CONTEXT_V1_HISTORICAL` and
is never aggregated into V2 readiness.  CUR03 values are `SAME` or `CHANGED`;
`NO_PRIOR_START` is null-by-design and `UNKNOWN` is unresolved.  Raw declared
jockey names are not a CUR03 coverage proxy.

The H2-C05 data gate remains a reporting gate, not feature activation.  Until
at least one genuine prospective V2 observation exists for each of 大井, 船橋,
川崎, and 浦和 it is `NOT_READY`; satisfying that minimum only yields
`ELIGIBLE_FOR_READINESS_REAUDIT` and never starts H2-C05 automatically.
