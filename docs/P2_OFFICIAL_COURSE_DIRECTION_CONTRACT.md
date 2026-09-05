# P2 Official Course Direction Contract V1

## Purpose and boundary

`P2_OFFICIAL_COURSE_DIRECTION_V1` supplies the V1 categorical target-race
`direction` only when it is established by an approved official source.  Its
status is `OFFICIAL_STATIC_COURSE_REFERENCE`: course direction is a
course-definition property, not an outcome-derived value or a model feature
selected from performance.

The versioned mapping and raw-source SHA-256 provenance are stored in
[`P2_OFFICIAL_COURSE_DIRECTION_V1.yaml`](../configs/features/P2_OFFICIAL_COURSE_DIRECTION_V1.yaml).
Historical canonical directions are QA comparators only and are never a source
for this mapping.

## Resolution order

1. D1 — an explicit official pre-race direction (`左` or `右`) is accepted.
2. D2 — if D1 is absent, resolve with the approved static official mapping.
3. D3 — if neither can establish direction, raise
   `BLOCK_DIRECTION_UNRESOLVED`.

If D1 and D2 both exist but disagree, raise `BLOCK_SOURCE_CONFLICT`.  A raw
course-layout token (`外` or `内`) is never a direction source and cannot be
used as a fallback.

## Frozen mapping

- 川崎, 船橋, 浦和: official fixed direction `左`.
- 大井: only the listed official-distance allow-list is supported. `1650m` is
  `左`; `1000`, `1200`, `1400`, `1500`, `1600`, `1700`, `1800`, `2000`,
  `2400`, and `2600m` are `右`.

There is no “all other Ohi distances are right” default.  Any unknown or newly
listed Ohi distance blocks target-race inference until an explicitly versioned
official-source amendment is completed.

## Provenance and audits

The four official course pages are raw-archived under
`data/raw/official_course_direction/`; each mapping source records URL,
capture time, archive path, and SHA-256.  M12B-R2 verified zero mismatches for
officially mapped historical Nankan combinations, while retaining their
`QA_ONLY_NOT_MAPPING_SOURCE` role.  The saved 2026-08-20 Kawasaki 6R–11R T15
cards resolve as `左` via D2; their displayed `外` layout was not used.

## Exclusions

This contract performs no model training, prediction, performance evaluation,
result/payout access, or ROI computation.  It does not freeze T15, a Primary
gamma, or a probability-edge claim.
