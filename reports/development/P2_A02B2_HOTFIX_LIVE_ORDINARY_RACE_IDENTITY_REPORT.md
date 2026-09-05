# P2-A02B2-HOTFIX Live Ordinary-Race Identity Parser Report

## Status
`HOTFIX_IMPLEMENTED_LIVE_RECHECK_REQUIRED`.

## Root cause
The official adapter required the display-title `race_name` alongside canonical race identity fields. The ordinary race page instead supplied a class/conditions-only title.

## Resolution
`race_name` is nullable. The required bootstrap fields are date, venue, race number, scheduled post time, distance, surface, and field size. The observed class-only title is retained as `conditions_raw`; it is not converted into an invented race name.

## Live bootstrap check
One direct entry-page request was made before this hotfix. The old parser stopped at `distance_m` before its capture routine saved raw bytes. The request is not repeated under the one-fetch limit. The corrected bootstrap path is validated with an explicitly synthetic fixture containing only the user-reported identity fields; a live-response confirmation remains required in the next user-run opportunity. No odds or result pages were fetched, and no snapshot, prediction input, or feature-store record was created.

## Test scope
The named historical fixture remains covered. The ordinary-race fixture verifies nullable `race_name`, separate `conditions_raw`, canonical required fields, and fixture-only bootstrap operation.

## Operational status
Foreground only; no background or child processes. T-15 remains an engineering candidate and is not frozen.
