# TRAINING_DATA_CONTRACT_V1

Status: `FROZEN_FOR_IMPLEMENTATION`

## Target and source-date rules

Development targets must not exceed `2026-07-31`.  Attempted inclusion of a later target is a failure, not a silent exclusion.  Result-derived source rows require `source_race_date < target_race_date`.

## Eligible development universe

Eligibility requires a South Kanto race identity, a valid starter universe, a valid official outcome, and an exact unordered Top3 set.  `field_size` may describe the universe but is not authorized as a B0 predictive feature.  The four outer folds and the precise provenance scaffold are fixed in the companion JSON.

## Scope

No model fitting, calibration, threshold selection, ROI analysis, or feature-performance selection is authorized by this contract.  The successor pipeline must retain `target_race_key`, target/as-of dates, maximum result-source date, fold, feature-manifest hash, source-DB hash, and a cold-start marker.
