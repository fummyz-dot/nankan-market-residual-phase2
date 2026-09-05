# P2 History Context DB Contract

## Scope
`db/p2_history_context.sqlite` is a Phase 2-owned, raw-semantic-preserving historical context store. It contains the 2020-01–2026-07 flat NAR corpus only: South Kanto targets plus the ten P2-M00 classified other-flat NAR venues. It is not a model dataset, does not approve `P2_XVENUE` model use, and does not change the South Kanto-only prediction/loss/evaluation boundary.

## Identity and race keys
- `identity_version`: `P2_HORSE_IDENTITY_V1`.
- Horse identity is the SHA-256 digest of the UTF-8 serialization `exact_raw_馬名 + "\\x1f" + YYYY-MM-DD生年月日` after the P2-M00 raw-field trimming rule only. No Unicode normalization, fuzzy matching, or name-only fallback is allowed.
- `race_key` is `P2_RACE_V1::YYYY-MM-DD\\x1fexact_raw_venue\\x1fdecimal_race_number`.
- The digest is stored with `horse_name_exact` and `birth_date`; `rename_link_status` is always `NOT_RESOLVED` in this version.

## Source and provenance
The immutable source is `reference/v1/data/raw_nar/zips/race/`. Every normalized race and runner stores a `source_member_id` and `source_row_number`; every member links to its archive path and SHA-256. The source cutoff is `race_date <= 2026-07-31`.

## Temporal and prohibited use
For future historical feature construction, only `history.race_date < target.race_date` is permitted. Same-calendar-date history is prohibited pending a timestamp-safe ordering contract. `last_nankan_date_metadata` is metadata only and is feature-use prohibited. The DB stores historical results because they belong to completed past races; no current target-race finish, result, time, margin, last-3F, payout, or odds may enter a feature join.

## Venue and event isolation
`NANKAN_TARGET` and `OTHER_FLAT_NAR` are included. `BANEI` and `UNKNOWN` are excluded from the formal DB. Raw race type, conditions, and race name remain untransformed. No class mapping or event semantic promotion is performed here.

## Rebuild and immutability
The formal DB is promoted from `db/.p2_history_context.sqlite.tmp` only by atomic rename after all validations pass. Existing formal or temporary files are never silently overwritten. A rebuild needs explicit operator cleanup and a fresh fully validated build. V1 original and `reference/v1/` remain read-only.
