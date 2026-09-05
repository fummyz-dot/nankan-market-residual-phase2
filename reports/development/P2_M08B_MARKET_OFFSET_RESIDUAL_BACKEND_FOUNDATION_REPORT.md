# P2-M08B — LightGBM Market-Offset Race-Softmax Backend Foundation

## STATUS
`READY_FOR_P2_M09_H1_LEGACY_RESIDUAL_DEVELOPMENT`

## Backend and objective
LightGBM 4.7.0 is frozen as the sole CPU `LIGHTGBM_GBDT` backend. The custom score is `gamma*log(q)+f`; its exact gradient is `p-y` and its frozen LightGBM-compatible Hessian is `p*(1-p)` (`DIAGONAL_SOFTMAX_HESSIAN_APPROX_V1`). LightGBM native `init_score` receipt was verified, so the chosen implementation is `NATIVE_INIT_SCORE_V1`; persisted raw LightGBM predictions are treated as residual score `f` and the common probability layer restores the Market offset at inference.

## Engineering checks
The analytic gradient finite-difference maximum was 1.47e-10; diagonal-Hessian maximum was 5.04e-11. Zero residual returned the calibrated Market and gamma=1 returned q within 1e-12. Save/load and repeated engineering fixture training passed deterministically. These are engineering fixtures, not historical H1 performance results.

## FS00 frame and protocol
The frame contains 833 historical reference races / 9522 runners and exactly 119 FS00 columns. Market is offset metadata only. Three pooled nested walk-forward folds and exactly six shallow/L2-regularized H1 configurations are frozen; no configuration was evaluated.

## Evidence limitation
Historical Market remains `HISTORICAL_MARKET_TIME_UNKNOWN` and development-reference-only. T-15 and the Primary gamma parameter remain unfrozen. Prospective stabilization outcomes, payout, ROI, Keibabook, and P2 new-feature performance were not used.
