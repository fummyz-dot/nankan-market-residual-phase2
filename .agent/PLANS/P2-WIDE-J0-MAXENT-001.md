# P2-WIDE-J0-MAXENT-001 — Projected Market Maximum-Entropy Top3 joint

## Objective

Lift each immutable J0-projection `q_star` to the unique maximum-entropy
distribution over unordered Top3 subsets. This is a deterministic Market joint
reconstruction and creates neither J1/D1 offset nor an operational model.

## Inputs and boundary

- Read only `q_star`, `pi_star`, roster, canonical pairs/subsets, and J0
  projection diagnostics from the completed projection Parquet.
- Reconstruct and validate its incidence ordering, but never rerun or modify a
  projection.
- Build/verify all MaxEnt joints before opening development true-Top3 labels.
  Labels are then used only for the registered Set NLL, binary guardrails, and
  pair-CE identity audit; August remains absent.

## Numerical procedure

1. Use pivoted QR of `A.T` with a machine-precision/shape rank tolerance to
   select independent equality rows deterministically.
2. Starting at the retained feasible `pi_star`, minimize `sum(xlogy(pi,pi))`
   by SLSQP only, with fixed `ftol=1e-12`, `maxiter=10000`, `[0,1]` bounds,
   rank-independent equalities, and the specified gradient guard.
3. Verify full `A*pi0/3=q_star`, mass/bounds, pair-hit and horse-Top3
   marginals. Fail closed; no alternate solver exists.
4. Persist entropy, support, stationarity, and full subset/pair outputs. If a
   true official set has structural zero support, record the support block and
   do not advance to J1.

## Exclusions

No q-star modification, entropy weight/temperature/smoothing parameter,
outcome-driven tuning, J1 beta/offset, D1 training, model selection,
economic analysis, or LIVE/WIDE_OPS/Policy/DEV-LIVE change.

## Acceptance

All 481 / 29,136 frozen records are represented, the complete-marginal
identity and `pi_star` source contract hold, max entropy preserves each
`q_star` within `1e-8`, and targeted rank/MaxEnt/support/leakage tests and a
deterministic rerun pass. J1 is only a comparator preregistration role and is
not implemented by this job.
