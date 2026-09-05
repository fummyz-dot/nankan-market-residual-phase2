# Phase 2 Protocol Amendment Log

## P2-AMEND-001 — Speed Main course-only promotion

- **Reason:** P2-M04A's pre-registered 2025 validation gate failed: going-adjusted
  S3 MAE was 1.245570 versus 1.239851 for the pre-specified course-only reference.
- **Information seen:** 2021–2024 selection metrics, 2025 validation metrics, and
  the 2026-01 through 2026-07 diagnostic metrics.
- **Changed item:** `P2_SPEED_STANDARD_MAIN_V1` is the separately versioned,
  pre-specified `COURSE_ONLY_ALL_HISTORY` reference. It has no going adjustment.
- **Not changed:** P2-M04A's selected S3 artifact remains the immutable historical
  failure record. No lookback, lambda, hierarchy, class adjustment, or estimator
  family was searched or revised.
- **Hypothesis status:** `P2_SPEED_GOING_ADJUSTMENT_V1 = REJECTED_NOT_SUPPORTED`.
  This does not reject the broader speed block.
- **Confirmatory rule:** A new, prospectively accumulated development period is
  required before any confirmatory claim. Historical 2025 and 2026-07 data may
  not be reused for confirmation of this amended candidate.
