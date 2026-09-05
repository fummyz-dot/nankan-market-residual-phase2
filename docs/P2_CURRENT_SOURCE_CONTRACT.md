# P2 Current Source Contract

P2_CURRENT uses only Nankankeiba / official NAR-derived pre-race content. Raw bytes, HTTP metadata, URL, capture time, and SHA-256 are retained in `source_captures`; every curated snapshot links to that capture. Keibabook, other P2X sources, final/result pages, and historical bodyweight values are not P2_CURRENT sources.

Availability evidence is one of `PUBLISHED_AT_CONFIRMED`, `OBSERVED_IN_PREDECISION_RAW_CAPTURE`, or `NOT_PROVEN_PREDECISION`. A raw capture proves only `available_by <= captured_at`; an absent published time is stored as NULL and never fabricated. Only the first two statuses can later support activation at the applicable decision time.

Current and Market data remain physically separate: current snapshots contain only allow-listed current values. A WIN roster may be compared for snapshot quality, but odds, popularity, q, or trajectory fields never enter P2_CURRENT. T15 is `ENGINEERING_CANDIDATE_NOT_FROZEN`.

## Pre-race roster status

The exact official detailed-card token `取消` is normalized only as the
target-roster status `PRE_RACE_WITHDRAWN`. It is inspected before the normal
active-runner parser: the raw row and official horse provenance remain auditable,
but it is excluded from P2_CURRENT active runners, target FS04 rows, Candidate,
Market, and active field size. This is not a result-status mapping and does not
alter historical `NONSTARTER` semantics. Any other explicit pre-race status
token blocks instead of being generalized or stripped. At T15, a withdrawn
number present in either CURRENT or WIN Market is a
`T15_WITHDRAWN_ROSTER_CONFLICT`.
