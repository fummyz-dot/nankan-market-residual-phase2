# P2 Speed Feature Contract

## Scope and status

`P2_SPD` is built only from `P2_SPEED_STANDARD_MAIN_V1` under P2-AMEND-001.
It is `PROVISIONAL_DEVELOPMENT_FEATURE`, not confirmed Primary use. The
observation table records post-race facts; the feature table is a separate
pre-race target-runner table. All historical source observations require
`source.race_date < target.race_date`.

## Source and exclusions

Source observations are finite `speed_z` records from NANKAN_TARGET,
course-only standard time, and non-exchange races. Other-flat, Ban'ei, any
exchange observation, current-race outcomes, same-day observations, class or
rating inputs, going-conditioned variants, time decay, distance-similarity
matching, odds, Market, payout, and ROI are excluded.

## Fields

| Feature | Formula / missing rule |
|---|---|
| `speed_prior_obs_count` | Number of eligible strictly-prior observations. |
| `days_since_last_speed` | Target date minus latest eligible observation date; NULL when no history. |
| `speed_cold_start_flag` | True iff prior count is zero. |
| `speed_last_z` | Latest strictly-prior eligible `speed_z`; NULL when cold. |
| `speed_recent3_mean_z`, `speed_recent5_mean_z` | Arithmetic mean of latest up to 3/5 values; available with at least one. |
| `speed_recent5_best_z` | Maximum of latest up to 5 values. |
| `speed_recent5_dispersion_z` | Population SD (`ddof=0`) of latest up to 5 values; NULL below two. |
| `speed_recent3_trend_z` | OLS slope for chronological latest 3 values with x=[0,1,2]; NULL below three. |
| `speed_exact_course_*` | Same formulas/counts using only exact `(venue, distance_m, surface, direction)` matches; missing direction never matches by inference. |

`speed_recent3_count`, `speed_recent5_count`, and
`speed_exact_course_recent3_count` are count metadata. No aggregate is zero
imputed at a cold start. No quality weighting or clipping is applied.

## Availability and leakage rule

For every target calendar date D, all runner feature rows are locked from state
through D-1 before D observations are added. Exchange target races may receive
features, but exchange results never enter later Main speed history. This block
must not access a Market or class dataset. New prospective development data is
required before any confirmatory use of this amended block.
