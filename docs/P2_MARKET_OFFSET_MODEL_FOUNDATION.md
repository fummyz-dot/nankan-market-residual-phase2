# P2 Market-Offset Model Foundation

The future WIN engineering-gate probability form is `MARKET_OFFSET_RACE_SOFTMAX`:

`score_ri = gamma * log(q_ri) + f_theta(x_ri, R_r)` and `p_ri = softmax_r(score_ri)`.

`gamma = exp(alpha)` is positive and may be estimated only inside a future training fold. The market-only comparator is the same family with `f_theta=0`. The primary future loss is race-equal soft-target multinomial log loss. M08A freezes the Market-only q normalization and `POWER_GAMMA_V1` method; its historical `MARKET_TIME_UNKNOWN` gamma is engineering diagnostic only and cannot freeze the actual T-15 parameter.

WIN is an engineering gate only. WIDE and TRIO remain independent scientific hypotheses. Feature candidates are exactly M06's FS00–FS04; no backend family or performance search is authorized here. T-15 remains an engineering candidate, not frozen.

M08B freezes the sole H1 backend as `LIGHTGBM_GBDT` with exact race-softmax gradient and `DIAGONAL_SOFTMAX_HESSIAN_APPROX_V1`, native LightGBM Market init-score offset, FS00-only input, three nested historical walk-forward folds, and exactly six legacy residual configurations. It performs no historical residual performance comparison; that registered comparison starts in M09.
