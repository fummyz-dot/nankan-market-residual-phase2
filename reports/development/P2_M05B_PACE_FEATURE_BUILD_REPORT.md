# P2-M05B — Pace Feature Build Report

## 1. STATUS

`READY_FOR_P2_M06_FEATURE_INTEGRATION_FOUNDATION`

## 2. Frozen Main pace sources

`P2_PACE_MAIN_V1` uses only M05A-approved NAR runner last-3F relative observations and exact race-level pace balance. Its model-use status remains `PROVISIONAL_DEVELOPMENT_FEATURE`.

## 3. Runner closing observations

Safe runner last-3F observations: 244494. Main-history eligible non-exchange observations: 243152.

## 4. Closing history features

All 250093 South Kanto target-runner rows have a pre-race feature row. Closing cold starts: 19557. Last/recent aggregates use at most 3 or 5 strictly prior eligible observations, without zero imputation.

## 5. Race pace-balance normalization

Raw exact pace-balance observations: 16959. Strict-prior course-relative robust-z observations: 16866. The fixed hierarchy is L1 course, L2 venue-distance-surface, L3 distance-surface, then L4 global; location is median and scale is MAD with a 0.25-second floor (used for 3 race observations).

## 6. Pace-exposure history

Pace-balance cold starts: 25456. This block represents prior race pace environments experienced by a horse; it is not a runner early-speed, pace-pressure, or running-style measure.

## 7. Cold start and transfer horses

Prior history is absent as NULL with explicit count/flag metadata. Other-flat history does not seed a South Kanto Main pace feature.

## 8. Exchange policy

Exchange targets retain pre-race rows, but exchange observations used in Main history: 0.

## 9. NAR runner-corner exclusion

No runner-corner feature was generated. M05A's `NOT_MODEL_READY` status is retained.

## 10. Runner first-3F external boundary

No NAR runner first-3F was fabricated. Keibabook runner first-3F/corner/pace sources are external-only and were not opened.

## 11. Same-day safety

Date-block processing locks every date's target features before adding that date's observations. Same-day and current-race source rows used: 0.

## 12. Extreme values

No clipping was applied to pace observations. Robust-z extremes are retained for a later separately contracted robustness decision.

## 13. Determinism

Two independent rebuilds produced the identical logical feature hash: `eae8fd93990f2746a69ff5daecd185d44de2fd4eb61a67d32457f84aa93079db`.

## 14. Model-use status

`PROVISIONAL_DEVELOPMENT_FEATURE`; no Market, odds, payout, ROI, residual-performance, Speed, or Class input was accessed.

## 15. Next stage

Proceed to `P2-M06 Feature Integration Foundation` under explicit strict-as-of and source-boundary contracts. This completion does not authorize model training or evaluation.
