# P2-M12B-RESUME — Online Shadow Pipeline Blocker Report

## STATUS

`BLOCKED_IN_P2_M12B` before online feature materialization, model training, or prediction work.

## Established R1 prerequisite

R1 identity recovery remains valid: all 70 Kawasaki 6R–11R T15 runners have an official exact identity, with 69 historical matches, one genuine cold start, and zero unresolved/collision.

## Blocking source semantic

Every saved official T15 card states distance and the course-layout token `外`, but none states the frozen V1 `direction` value `左` or `右`. Historical Kawasaki FS04 rows use `direction=左`. `direction` is one of the 119 frozen V1 tree columns, so emitting `外`, `__UNKNOWN__`, or deriving `左` from venue/course layout would not preserve frozen V1 semantics. The six-row raw audit is `audit/data/p2_m12b/current_target_static_source_audit.csv`.

## Consequence

Historical online parity cannot be safely attempted, so later checkpoints were not started. No `DEV-LIVE-V1` model, prediction schema, inference command, bundle extension, decision template, replay, result access, performance, payout, or ROI operation was performed.

## Required recovery

Provide an official pre-race source with explicit V1-compatible direction, or approve a separately audited official course-direction mapping before any online feature implementation. A venue-based or `外`-based heuristic is not a permitted fallback.
