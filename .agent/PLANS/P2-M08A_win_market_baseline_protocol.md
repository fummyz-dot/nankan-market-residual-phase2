# P2-M08A — WIN Market Baseline Normalization & Calibration Protocol

## Inputs

- Read-only V1 historical Market DB, Phase 2 prospective snapshot DB, M07 frozen race universe and separate outcome semantics.

## Outputs

- Shared deterministic WIN odds normalization, power-gamma calibration, and Market-only loss core.
- Separate historical-reference and prospective-stabilization q datasets, frozen contracts/configuration, audits, manifests, tests, and report.

## Invariants

- Historical `MARKET_TIME_UNKNOWN` is engineering reference only, never T-15 evidence.
- Prospective stabilization snapshots receive no outcome join or performance evaluation.
- H0 uses Market and M07 soft labels only: no P2 features, Keibabook, payout, ROI, or residual model.
- Snapshot roster is capture-time only; later scratches never rewrite past q.

## Acceptance

- Valid q is positive and sums to one exactly within `1e-12`; incomplete/invalid snapshots are rejected.
- Power-gamma solver is deterministic and method-frozen, while actual Primary T-15 gamma remains unfrozen.
- Manual parity, row-order invariance, logical rebuild determinism, and source-boundary audits pass.
