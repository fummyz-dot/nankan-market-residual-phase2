# P2 Historical Context Contract

## Purpose
Define the Phase 2-owned context store, independent of immutable V1 databases. The operational schema and rebuild rules are fixed in `P2_HISTORY_CONTEXT_DB_CONTRACT.md`.

## Draft schema
`db/p2_history_context.sqlite` contains:
- `build_metadata(key, value)`
- `source_archives` and `source_members` with raw archive/member SHA-256 lineage
- `horses(horse_identity_key, horse_name_exact, birth_date, identity_method, identity_version, identity_quality, rename_link_status, first_observed_race_date)`
- `races(race_key, race_date, venue, venue_class, race_number, raw race semantics, source_member_id, source_row_number)`
- `race_runners(race_key, horse_identity_key, horse_number, historical outcome fields, source_member_id, source_row_number)`
- `target_horses` with metadata explicitly prohibited from historical feature use
- `identity_audit` with per-identity source-row and collision status

## Provenance and isolation
Every stored row must trace to archive and ZIP member. V1 DBs remain read-only references. Only `race_date <= 2026-07-31` is in the historical-development corpus. The 128 later V1 history rows are permanently excluded. A formal DB is created only by validating `db/.p2_history_context.sqlite.tmp` and atomically promoting it; existing formal DBs are not silently replaced.

## Venue/event status
Use `NANKAN_TARGET`, `OTHER_FLAT_NAR`, `BANEI`, or `UNKNOWN` as observed venue classes. `BANEI` never enters flat context. Raw event types remain explicitly classified; unknown/non-standard events must not be silently promoted into standard-race features.

## Non-approval
This schema is a storage/provenance foundation only. `P2_XVENUE modeling` remains `NOT_YET_APPROVED_FOR_MODEL_USE`.
