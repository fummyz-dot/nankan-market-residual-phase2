# P2-M12B-R2 Official Course Direction Mapping Report

## STATUS

`READY_TO_RESUME_P2_M12B_FROM_ONLINE_FEATURE_MATERIALIZATION`

## Mapping

`P2_OFFICIAL_COURSE_DIRECTION_V1` records raw-archived official course-page
provenance for all four Nankan venues. Kawasaki, Funabashi, and Urawa resolve
to `左`. Ohi uses only its official distance allow-list: 1650m is `左`; 1000,
1200, 1400, 1500, 1600, 1700, 1800, 2000, 2400, and 2600m are `右`.

## Source semantics

The resolver gives priority to an explicit official pre-race direction. If it
is absent, it uses `OFFICIAL_STATIC_COURSE_REFERENCE`; an unknown distance
blocks, and explicit/static disagreement blocks. `外`/`内` is retained as raw
layout context but never converted into direction.

## Today

The six saved 2026-08-20 Kawasaki 6R–11R predecision-valid T15 cards all
resolve to `左` through the official static source. Unresolved count is zero.

## Historical parity

Historical canonical direction values were QA only. Every officially mapped
venue/distance combination had zero mismatch. No historical value was used to
define or override the mapping.

## Safety and exclusions

Ohi 1650 resolves left, listed right-direction distances resolve right, and an
unlisted Ohi distance raises `BLOCK_DIRECTION_UNRESOLVED`. No result source,
model training, prediction, performance calculation, payout, or ROI operation
was executed.

## Next

Resume the approved M12B at online feature materialization and FS04 178-column
historical parity. Do not redo identity recovery or course-direction research.
