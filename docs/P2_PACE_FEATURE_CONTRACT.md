# P2 Pace Feature Contract

`P2_PACE_MAIN_V1` is a runner-level, strict-as-of historical block with
`PROVISIONAL_DEVELOPMENT_FEATURE` status. It has two meanings: runner closing
relative performance and the pace environments the runner previously
experienced. The latter is not an early-speed, pace-pressure, or front-running
ability estimate.

Closing observations require safe non-exchange NAR `last_3f`, finite
median-relative closing advantage, and finite within-race rank percentile.
Pace-balance observations require exact M05A race balance and a strictly-prior
course median/MAD standardization. The fixed hierarchy is course
(venue/distance/surface/direction), venue/distance/surface, distance/surface,
then global; location is median, scale is `1.4826 * MAD`, minimum five, and
floor 0.25 seconds.

For every target date D, features use source observations through D-1 only.
Current-race and same-day source outcomes, exchange/other-flat observations,
NAR runner corners, runner first-3F, Keibabook, full-lap shape, speed/class
data, Market data, clipping, decay, distance similarity, and style labels are
prohibited. Cold starts retain NULL aggregates rather than zero values.
