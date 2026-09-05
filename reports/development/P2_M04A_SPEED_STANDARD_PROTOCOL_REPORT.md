# P2-M04A Speed Standard Protocol Report

## 1. STATUS
`SPEED_STANDARD_WEAK_REVIEW_REQUIRED`. The registered 2025 validation gate was not met. No additional
lookback, estimator family, class adjustment, or Market-informed change was run.

## 2. Finish-time semantics and timing universe
Only finite, positive `FINISHED` runner times are eligible. The race-clock
target is the median valid finisher time, with at least three valid finishers
and at least 50% of the recorded field. The prototype contains
21849 Nankan races and 250093 runner rows.

## 3. Course, going, and robust scale protocol
Course fallback is L1 venue/distance/surface/direction, L2 venue/distance/surface,
L3 distance/surface, L4 surface, L5 global; each uses median location with lambda
20 shrinkage. Going uses strictly-prior residuals with venue+going, going, then
zero fallback. Speed scale is strictly-prior `1.4826 * MAD`, floored at 0.50,
with L1/L2/L3/global fallback. Unknown going is never inferred.

## 4. Registered selection and validation
S1 (365d) selection MAE 1.471924;
S2 (730d) 1.427920; S3 (all history)
1.411846. S3 was selected solely on
2021–2024. Its one-time 2025 MAE was 1.245570, versus 1.239851 for fixed
`COURSE_ONLY_ALL_HISTORY` (delta +0.005719). The frozen 2026 diagnostic
was 1.261884 versus 1.272504.

## 5. Strict-as-of and source isolation
All races on date D are scored before D's observations update any state. Exchange
races can receive a figure but never update course, going, or scale state.
Other-flat, Ban'ei, class/rating, odds, and Market data are excluded.

## 6. Data quality and next stage
Thirty-six raw going values are unknown and receive the explicit zero correction.
Because 2025 did not beat the reference, M04B must not start under this protocol.
The next action is a documented review or amendment, not additional search.
