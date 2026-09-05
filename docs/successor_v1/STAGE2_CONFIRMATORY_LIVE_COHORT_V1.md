# Stage2 Confirmatory Live Cohort V1

Status: **FROZEN BEFORE CONFIRMATORY LIVE OUTCOME**

JOB007R3 is accepted as a development locked replay and implementation validation.
Its 2026-08-01 through 2026-09-03 rows do not count toward formal Stage2 support
or the final bootstrap.

The confirmatory cohort begins with the first eligible race on or after
2026-09-07. A confirmatory row must have an actual live prediction artifact
frozen no later than the T15 decision time. A later reconstruction can never be
promoted into the confirmatory cohort.

Development locked-replay rows may initialize the already-frozen prequential
gamma/beta calibration and the date-causal EB state, but same-day updates remain
prohibited.

Only CONFIRMATORY_LIVE_PREDECISION rows count toward the frozen formal gate:
100 races, 12 race dates, and 10 races in each of 大井 / 川崎 / 浦和 / 船橋.
No automatic performance unblinding occurs.

The user continues to operate only ./specialized-collect. Stage2 scoring must
run in an isolated subprocess. The Stage2 worker reads local stores only,
performs no network access, freezes predictions before the T15 deadline, and
never writes the market database. Worker failure must not stop collection.

The two pending 2026-09-03 development predictions may be reconciled when local
official result state appears, but remain development-only.

Economic edge and betting remain prohibited.
