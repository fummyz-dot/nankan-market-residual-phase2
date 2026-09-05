# P2_CURRENT previous-jockey identity contract V1

## Scope

`CUR03` (`current_jockey_change_from_last_nankan_flag`) remains
`REGISTERED_NOT_ACTIVATED`.  It is P2_CURRENT research/context provenance
only.  It is not an FS04 feature, DEV-LIVE-V1 input, recommendation-policy
input, or race-day gate.

## Identity authorities

- Target horse identity is read only from the immutable Main analysis bundle's
  `main_identity_audit`, keyed by exact `race_key` and `horse_number`.
- Current jockey identity is exactly one same-runner-row official
  `/kis_info/<official_id>.do` anchor in the retained CURRENT card.
- Prior jockey identity is the normalized live-history delta official jockey
  ID only when it belongs to the selected canonical prior race.

No horse-name matching, jockey raw-name matching, alias matching, affiliation
stripping, or horse-detail refetch is an authority for CUR03.

## Previous-start definition

CUR03 means **change from the LAST NANKAN ACTUAL START**, not the last NAR
start.  Eligible venues are 大井, 船橋, 川崎, and 浦和 only.  The selected race
must have `race_date < target_date`; same-day and future races are prohibited.

The existing approved `starter_status()` semantics determine actual starts:
`STARTER_VALID_FINISH` and `STARTER_NO_VALID_FINISH` are eligible;
`NONSTARTER` is excluded.  An unclassifiable newer candidate is `UNKNOWN`, not
`NO_PRIOR_START`.

Base history and normalized delta are deduplicated by canonical `race_key`
before selecting the latest eligible race.  Ordering is descending
`race_date`, then `race_number`, then `race_key`; race-number ordering never
selects a same-day race because same-day candidates are excluded first.  For a
single selected race, delta supplies its official jockey ID when available.

## Derivation

- Equal current/prior official IDs: `SAME`, CUR03 `0`.
- Different current/prior official IDs: `CHANGED`, CUR03 `1`.
- Exact target identity plus a completed Nankan search with no eligible actual
  start: `NO_PRIOR_START`, CUR03 `null`.
- Any identity, source, status, or official-ID ambiguity: `UNKNOWN`, CUR03
  `null`.

`NO_PRIOR_START` is not `SAME`; `UNKNOWN` is not `CHANGED`.

## Evidence versioning

New sidecar evidence is `p2_current_research_evidence_v2` with payload
`p2_current_research_payload_v2` and context version
`P2_CURRENT_JOCKEY_CONTEXT_V2`.  Existing V1 evidence remains immutable
historical provenance.  This contract performs no historical rebuild.
