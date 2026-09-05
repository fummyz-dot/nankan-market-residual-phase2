# P2-MKT-TRAJ-LL-V1-CONFIRMATORY-PROTOCOL-FREEZE-033

## Inputs

- Immutable Main Recommendation Evidence and append-only WIN trajectory mark events.
- Frozen DEV-LIVE-V1 model, FS04 hash, WIN gamma, and approved odds normalization.

## Outputs

- Immutable protocol manifest and a separate V1 race-level cohort evidence table/artifact namespace.
- Effect-blinded enrollment/status and one-per-venue blinded re-estimation support.
- A final-only, cluster-aware WLS / wild-bootstrap-t analysis gate.

## Invariants

- No result, payout, settlement, ROI, model, policy, or feature access.
- Pre-freeze events never enter membership; T15 must be strictly after protocol freeze.
- Funabashi is Gate 1; Ohi effect output remains sealed until Gate 1 existence support.
- No effect statistic is emitted during accumulation or re-estimation.
- Immutable/idempotent evidence and final analysis records; source conflicts fail closed.

## Validation

- Synthetic unit tests for freeze, eligibility, no-peek status, re-estimation, final gates, venue gate, and decision states.
- Existing trajectory/lead-lag regressions and compileall.
