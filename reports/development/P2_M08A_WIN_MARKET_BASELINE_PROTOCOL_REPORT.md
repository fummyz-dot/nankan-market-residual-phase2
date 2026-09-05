# P2-M08A — WIN Market Baseline Protocol

## STATUS
`READY_FOR_P2_M08B_MARKET_OFFSET_RESIDUAL_BACKEND_FOUNDATION`

## Source classes
Historical reference has 1437 WIN races / 15825 runner rows and remains `HISTORICAL_MARKET_TIME_UNKNOWN`. Prospective stabilization has 4 complete timestamped WIN snapshots / 44 runner rows; fixture rows are excluded and outcomes are not joined.

## Normalization and calibration
`q_i=(1/o_i)/sum_j(1/o_j)` is positive and sums to one. Historical roster reconciliation is exact for all source races. `POWER_GAMMA_V1` was solved only as an engineering diagnostic: gamma=0.983655773069; raw LL=1.73603453891; calibrated LL=1.73590678871. This does not freeze a T-15 gamma.

## Safety
Dead-heat soft labels retain unit mass. Manual and row-order parity passed at <=1e-12. No P2 features, payout, ROI, or prospective stabilization outcome was used. T-15 remains an engineering candidate.
