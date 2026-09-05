# P2-M09 — H1 Legacy Market-Residual Historical Development Evaluation

Inputs are M08A q/gamma, the M08B LightGBM 4.7.0 FS00 training frame, the frozen six-config grid, and the frozen all-Nankan walk-forward folds. The run is sequential and checkpointed.

Invariant: every configuration/fold runs once; gamma is shared per fold/training scope; iteration 0 is the calibrated-Market candidate; selection pools every outer-validation race equally; no prospective, payout, ROI, or P2 new-feature source is allowed.

The output retains every configuration's predictions and metrics, spends the six-config budget exactly once, performs a post-selection date-block bootstrap, and records one excluded-from-selection real-fold deterministic repeat.
