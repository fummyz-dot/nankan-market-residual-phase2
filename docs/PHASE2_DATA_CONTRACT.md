# Phase 2 Prospective Data Contract

## Boundary
Prospective data is stored only in Phase 2 active namespaces. V1 databases and `reference/v1/` are never capture destinations. The Phase 2 Main candidate may use only Market plus NAR/NAR-derived data under an approved later protocol.

## Race identity
`canonical_race_key = race_date + venue + race_number`. The eligible venues are 大井, 船橋, 川崎, and 浦和. A Keibabook-specific race identifier is external metadata, not the canonical key.

The official-page identity fields required for a prospective bootstrap are `race_date`, `venue`, `race_number`, `scheduled_post_time`, `distance_m`, `surface`, and `field_size`. `race_name` is nullable: an ordinary conditions race can have no distinct race name. When the official title is only an observed class/conditions label (for example `Ｃ２(三)(四)`), preserve it separately as `conditions_raw`; never invent a race name or split an ambiguous named-race title into class fields.

## Timestamp semantics
`requested_at`, `captured_at`, `source_published_at`, and `scheduled_post_time` are distinct timezone-aware timestamps. Store UTC ISO-8601; the offset permits JST reconstruction. `source_published_at` is `NULL` when unknown and is never inferred from `captured_at`.

## Status semantics
Operational input status is one of `COLLECTED_OK`, `DATA_MISSING`, `USER_INPUT_MISSING`, `OPERATIONAL_MISS`, `SOURCE_UNAVAILABLE`, `HTTP_ERROR`, `PARSE_ERROR`, `STALE_CAPTURE`, `RACE_CANCELLED`, or `NOT_ELIGIBLE`. Missing input is not a model/betting decision.
