# V1 Reuse Policy

## Principle
Reuse engineering assets, not V1 conclusions or holdout status.

## Read-only reference root
All copied V1 material must live under `reference/v1/` and be treated as immutable.

## Reuse first
- race/runner join semantics;
- strict-as-of utilities;
- source provenance and hashing;
- V1 119-feature semantics for legacy residual baseline;
- manual loss implementations;
- race-date block bootstrap;
- pair/combo generation semantics;
- probability-normalization tests;
- prohibited-feature scans;
- permutation invariance tests;
- run-manifest patterns.

## Do not reuse as Phase 2 primary design
- V1 75/25 market/model blend;
- V1 final-holdout status as model-selection evidence;
- V1 final confirmation period as Phase 2 holdout;
- post-hoc venue/odds-band/threshold rescue rules.

## Parity requirement
Any V1 semantic reimplementation must include a parity test against frozen V1 data or outputs before Phase 2 modifications are introduced.
