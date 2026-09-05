# P2 Speed Standard Contract

`P2_SPD_MAIN_V1` is a South-Kanto-only chronometric protocol. It uses no
class/rating/odds/Market input. Under `P2-AMEND-001`, its provisional
development standard is `P2_SPEED_STANDARD_MAIN_V1`:
`COURSE_ONLY_HIERARCHICAL_ROBUST_STANDARD`, all available strictly-prior
history, hierarchical median course baseline, lambda 20, and robust MAD scale
(floor 0.50 seconds). Going adjustment is exactly `NONE`.

The M04A S3 hierarchical course-plus-going artifact remains preserved as the
historical validation failure. `P2_SPEED_GOING_ADJUSTMENT_V1` is
`REJECTED_NOT_SUPPORTED`: 2025 validation did not beat its pre-specified
course-only reference. This does not reject the broader speed block. The
amended Main standard is `PROVISIONAL_DEVELOPMENT_FEATURE`, not
`PRIMARY_CONFIRMED`; it requires a new prospective development period before a
confirmatory claim. Already-seen 2025 and 2026-07 data are prohibited for that
confirmation.

Every race on date D observes only states through D-1; D updates after all D outputs lock. Race clock target is median valid finisher time. Standard updates require >=3 valid finishers and >=50% of field size; exchange/bare-exchange updates are prohibited, though output speed observations are allowed. The course hierarchy is venue/distance/surface/direction, venue/distance/surface, distance/surface, surface, then global, with `n / (n + 20)` shrinkage.

`speed_seconds=standard_time_pre-finish_time_seconds`; positive is faster. `speed_z` is `ROBUST_STANDARDIZED_SPEED`, not a normality claim or CI. Current performance may define the figure but never its standard/scale. Future aggregation must use only `past_speed.race_date < target.race_date`; same-day and other-flat speed are prohibited.
