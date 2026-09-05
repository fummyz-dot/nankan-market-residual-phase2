# P2 Live History Freshness Contract

## Current status

`PASS` — strict-as-of state, normalized state, frozen-V1 person-category
semantics, and official-meeting accounting are established.  The former P7
text-semantic block remains in the audit history at
`audit/data/p2_m12b/P7_LIVE_INFERENCE_PRECHECK_BLOCKED.json`.

`P2NormalizedHistoricalAsOfProvider` is the approved read-only date boundary: base
rows are strictly earlier than a target date and normalized finalized live-
delta rows are strictly earlier than the same target date. It is shared by
V1, P2_CLASS, P2_SPD, and P2_PACE.

## Required gate before live shadow inference

The provider exposes their existing frozen raw entities and observation inputs
without copying target feature vectors or recreating four independent overlay
formulas. The 2026-06-30 base + compiled July delta simulation reproduced
FS04-178 on 44 runner rows with zero mismatches and maximum numeric difference
`5.000444502911705e-13`. For 2026-08-20, 192 prior delta races are visible,
no same-date row is visible, and maximum history date is 2026-08-19. For
2026-08-21, all 204 delta races through 2026-08-20 are visible.

The date boundary, M04 replay, July FS04 parity, August same-day tests, and
the P7 frozen-V1 person-category gate passed. Retained official pre-race cards
establish each observed August person by official ID and retain the official
compact display that is the exact frozen V1 token. Raw displays are preserved
separately; no shortening, marker removal, fuzzy matching, or category/model
change was introduced. The recovery audit is
`audit/data/p2_m12b/P7_V1_PERSON_CATEGORY_TEXT_SEMANTICS_RECOVERED.json`.

## Meeting-aware daily freshness boundary

Raw/normalized count equality is necessary but not sufficient.  Every normal
`live_history_update --through D` first discovers each South Kanto calendar
date after the latest `COMPLETE` / `NO_MEETING` ledger date through `D`.

- a date with an official meeting is `COMPLETE` only when every explicitly
  discovered race result URL is present in both raw and normalized history;
- a calendar date with no South Kanto meeting is recorded explicitly as
  `NO_MEETING`;
- failed discovery, partial raw accounting, or stale normalized accounting
  keeps the date non-fresh and blocks inference.

The append-only `meeting_history_ledger` in
`db/p2_live_history_delta.sqlite` retains date, official calendar status,
venues, exact expected/raw/normalized result URL sets and counts, status,
timestamp, and calendar provenance.  `LIVE_HISTORY_FRESH` is issued only
when `OFFICIAL_MEETING_HISTORY_COMPLETE=PASS` and
`NORMALIZED_CACHE_CURRENT=PASS`.  A target race on date `D` requires the
ledger through `D - 1`.

The 2026-08-24 recovery accounted 2026-08-21 Kawasaki (12 races) and
explicitly recorded 2026-08-22 and 2026-08-23 as `NO_MEETING`; raw and
normalized live delta both then contained 216 races and 2,257 runners, with
maximum history date 2026-08-21.  Evidence is under
`audit/data/p2_live_20260824_h1/`.

## Daily normalization boundary

`python3 -m src.operations.live_history_update --through YYYY-MM-DD` performs
incremental official calendar discovery, official ingestion, and rebuildable
normalized-cache refresh in the same foreground operation.  A newly committed
raw race has a durable checkpoint before the cache promotion; interruption
cannot make the day fresh, and restart exact-accounts that raw race before
continuing. New raw races are compiled into a staging copy, M02/M04/M05 state
is validated, and the normalized cache is atomically promoted. If that
derivation fails, raw provenance may remain committed but
`LIVE_HISTORY_NORMALIZATION_STALE` blocks `race-shadow`.

The refresh checks raw/normalized race and runner accounting on every run. An
unchanged, meeting-accounted delta is an explicit `IDEMPOTENT_NOOP`; only newly
appended race primitives are compiled before the derived-state refresh. Result
and reconciliation databases are not opened by this path; retained official
result raw may be reused as immutable official provenance.
