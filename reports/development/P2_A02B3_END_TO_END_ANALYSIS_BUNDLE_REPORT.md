# P2-A02B3 End-to-End Analysis Bundle Report

## 1. STATUS
`READY_FOR_P2_DATA_STABILIZATION_AND_MODEL_FOUNDATION`. Bundle foundation PASS.

## 2. Race used
2026-08-19 川崎5R, `race_name=null`, `conditions_raw=Ｃ２(三)(四)`, 11 runners.

## 3. Source resolution
The retained live-freshness output supplied the marked T15 source. Daily Keibabook ability and training JSON were discovered by schema/content and each resolved exactly one matching race.

## 4. T15 as-of enforcement
Only explicit `PRIMARY_CANDIDATE` rows with `T-15_ENGINEERING_CANDIDATE` were selected. T10/T05 rows existed (462) and were audited as prohibited, never selected by latest timestamp.

## 5. Bodyweight and market
Bodyweight is 11/11; WIN/WIDE/TRIO are 11/11, 55/55, and 165/165.

## 6. Keibabook and runner joins
P2X-O retains only A01 `EXT_OBJECTIVE` fields. P2X-S retains structured training without feature engineering. Keibabook trial/retraining-trial labels are tagged separately; unconfirmed ordinary past rows remain `UNKNOWN`, never promoted to official history. All 11 bodyweight runners joined ability and training by exact race identity plus horse number; horse name was not a primary key.

## 7. Eligibility and prohibited data
The draft rule classifies C2 as `ELIGIBLE`. No result, payout, post-primary market, or prohibited Keibabook field reached the bundle.

## 8. Schema, operation, and remaining gaps
The output follows `p2_race_analysis_bundle_v1`, has provenance hashes, and is suitable for a single ChatGPT upload. It contains no model, probability, edge, or ticket. T-15 and the eventual one-command wrapper remain unfrozen.
