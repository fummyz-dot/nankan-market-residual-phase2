# P2-A02B2 Live Freshness Probe Preparation Report

## Status
`READY_FOR_USER_LIVE_FRESHNESS_RUN`. The runner is a foreground one-command operation. This preparation job did not access a live race.

## Scheduler and capture behavior
The probe obtains scheduled post time, waits with a monotonic-clock abstraction for T-20/T-15/T-10/T-5, records missed marks without backfill, and bounds each direct fetch by timeout.

## Source and snapshot handling
Each mark re-fetches the race page, rechecks identity, applies the current-info allow-list, discovers odds URLs from DOM anchors, captures WIN/WIDE/TRIO bytes, hashes raw responses, and records HTTP/cache metadata. T-15 is `PRIMARY_CANDIDATE` with `LIVE_FRESHNESS_TEST`, never frozen.

## Failure, checkpoint, and output behavior
Failures are captured per mark and later marks continue. Successful marks are atomically checkpointed; existing successful checkpoints are not overwritten on resume. The run removes `RUNNING` and emits a terminal marker. The final JSON is separate from all model-analysis bundles.

## Offline validation
Mocked T20/T15/T10/T5 fixture flow passed; unchanged fixture bytes produced zero quote changes without any stale-data assertion. A synthetic WIDE timeout failed T20 and T15 continued.

## Remaining live-only unknowns
Freshness, cache behavior, displayed-time meaning, schedule changes, scratches, active runner universe changes, and actual timing must be observed only in the user live run.
