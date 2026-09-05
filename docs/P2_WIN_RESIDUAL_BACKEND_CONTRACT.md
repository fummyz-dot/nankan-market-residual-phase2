# P2 WIN Residual Backend Contract

`P2_WIN_RESIDUAL_BACKEND_V1` freezes the only H1 backend family as CPU `LIGHTGBM_GBDT` (LightGBM 4.7.0), with `MARKET_OFFSET_RACE_SOFTMAX_GBDT` probabilities. CatBoost, XGBoost, sklearn GBDT, neural networks, and backend performance comparison are outside the registered search budget.

For every contiguous race group, `z_i = gamma * log(q_i) + f_i` and `p_i = softmax_r(z_i)`. `q` is M08A's positive, unclipped raw normalized Market. Gamma is fitted only on the applicable training dates and is shared by the candidate and calibrated-Market comparator; no historical diagnostic gamma may be copied to a fold or the future Primary parameter.

The LightGBM custom objective receives the exact first derivative `p_i-y_i` of the sum of race soft-target losses. Its per-row Hessian is the frozen `DIAGONAL_SOFTMAX_HESSIAN_APPROX_V1`, `p_i(1-p_i)`. This is not the full softmax Hessian: its race-internal off-diagonal terms are intentionally unavailable to LightGBM's row-wise interface. Non-finite or negative Hessians fail.

The installed backend was verified to pass `init_score=gamma*log(q)` to the custom objective. The frozen implementation is `NATIVE_INIT_SCORE_V1`; persisted LightGBM output is treated as residual tree score `f`, and the common probability layer restores the Market offset at inference. It never calls a built-in binary probability method. With `f=0`, probabilities and loss equal calibrated Market and edge is zero within `1e-12`; with `gamma=1,f=0`, probabilities equal raw q.

`FS00_LEGACY` alone is available to this backend foundation: exactly 119 `V1__` model columns. q, log-q, odds, Market rank/popularity, labels, and keys are offset/label/metadata only, never tree features. Numeric missing values remain missing; categoricals use LightGBM native categorical inputs with vocabulary learned only from the training fold. `__MISSING__` and `__UNKNOWN__` are reserved; unseen validation/future values map to `__UNKNOWN__`. No target/frequency/ordinal encoding, global imputation, clipping, or residual-score clipping is allowed.

Prediction requires the approved snapshot-time active roster before scoring. Dropping a scratched horse after prediction and renormalizing survivors is prohibited. Payout, ROI, CORE thresholds, Keibabook, P2 new feature blocks, and prospective stabilization outcomes are outside this backend foundation.

The resulting model API is `predict_win_market_offset(feature_frame, q_frame, gamma, model_artifact)`, producing per-runner q, calibrated Market probability, raw/effective residual score, candidate race-softmax probability, and `edge_log_ratio=log(p_candidate/p_market_calibrated)`.

