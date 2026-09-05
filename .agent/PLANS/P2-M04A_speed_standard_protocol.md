# P2-M04A — Strict-As-Of Standard Time & Speed Figure Protocol Freeze

## Scope

Freeze one South-Kanto-only `P2_SPD_MAIN_V1` standard-time configuration from
the three registered lookback windows. Build prequential race/runner prototype
outputs only; M04B will aggregate strictly prior runner-speed history.

## Inputs

- Read-only `db/p2_history_context.sqlite`
- P2-M03B documentation as a boundary reference only; no class fields are read
  by the standard-time estimator.

## Invariants

- Nankan `FINISHED`, positive finite times and valid race identities only.
- Course target is the race median of valid finisher times, requiring at least
  three valid finishers and 50% of field size.
- Course/going/scale state is read through D-1 for every race on D, then D is
  appended as a date block.
- Course hierarchy, median-shrinkage lambda 20, going hierarchy, MAD scale,
  and scale floor 0.50 are fixed; only lookback is selected from 365/730/ALL.
- Exchange/bare-exchange races may receive output but never update baseline,
  going, or Main speed-scale state. Other-flat/Ban'ei, class, and Market inputs
  are excluded.

## Acceptance

- Exactly three lookbacks selected only by 2021–2024 race-equal MAE.
- Selected configuration is validated once against COURSE_ONLY_ALL_HISTORY on
  2025 and remains fixed for 2026 diagnostic.
- Same-day, exchange, other-flat, class, and Market prohibitions audit to zero.
- Prototype hashes, selected configuration, source/result registries, report,
  and run manifest are produced foreground with no child workers.

## Completion record

- Completed foreground on 2026-08-19 with no child or background process.
- The registered grid selected `S3` / ALL AVAILABLE HISTORY from 2021–2024
  only. The one-time 2025 comparison did not beat `COURSE_ONLY_ALL_HISTORY`.
- Final status is `SPEED_STANDARD_WEAK_REVIEW_REQUIRED`; this plan does not
  authorize an extra search or P2-M04B promotion.
