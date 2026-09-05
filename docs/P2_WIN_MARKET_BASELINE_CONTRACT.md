# P2 WIN Market Baseline Contract

WIN raw normalized Market is `RAW_NORMALIZED_WIN_MARKET_V1`: for finite decimal odds `o_i > 0`, `q_i=(1/o_i)/sum_j(1/o_j)`. q is positive, finite, unclipped, and must sum to one within `1e-12`. Overround is diagnostic only; invalid, missing, duplicate, or incomplete active-runner odds reject the whole snapshot without imputation.

The active runner set is the snapshot-time runner set. A scratch known before the snapshot is omitted before q normalization. A later scratch never retroactively rewrites that snapshot's q.

`POWER_GAMMA_V1` is the only calibration family: `p_i(gamma)=q_i^gamma/sum_j(q_j^gamma)`, with positive gamma fitted by deterministic derivative-root bisection and race-equal M07 soft-target multinomial log loss. The method is frozen. Historical `MARKET_TIME_UNKNOWN` gamma is engineering diagnostic only; the actual Primary snapshot gamma remains unfrozen.

`f_theta=0` returns the calibrated Market exactly. The future residual edge definition is `log(p_candidate/p_market_calibrated)`; Market-only edge is zero.
