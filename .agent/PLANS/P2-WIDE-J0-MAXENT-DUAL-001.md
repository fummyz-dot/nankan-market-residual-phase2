# P2-WIDE-J0-MAXENT-DUAL-001 — J0 support-face discovery and dual MaxEnt

## Objective

Solve the exact, pre-registered J0 MaxEnt problem for the immutable projected
Market marginal `q_star`. This replaces only the failed primal SLSQP numerical
parameterization with the specified LP support-face discovery and dual
exponential-family solver. No scientific target, Market input, or outcome use
changes.

## Inputs

- The J0 projection race artifact supplies authoritative canonical runner,
  pair, subset, `q_star`, and feasible `pi_star` values for 481 / 29,136
  development records.
- Baseline labels are deliberately unopened until every support face and dual
  joint has passed full-marginal verification.

## Solver contract

1. Determine independent full equalities from pivoted QR of `[ones; A].T`.
2. Use fixed HiGHS LPs for full-support and, where needed, support-union and
   face-relative-interior discovery.
3. On the established support only, remove the constant gauge with a
   difference-feature basis and solve the fixed L-BFGS-B dual with analytic
   gradient.
4. Verify full incidence residual, entropy versus the LP interior witness,
   stationarity, probability bounds, and the 2026-05-07 Funabashi 3R prior
   primal-failure regression before labels are opened.

## Exclusions

No q-star modification/reprojection/smoothing, Market recalibration, entropy
parameter, J1/D1 training, model selection, outcome-driven solver choice,
economic evaluation, or LIVE/WIDE_OPS/Policy/DEV-LIVE modification.

## Acceptance

Persist every support/face audit and every subset/pair marginal. If any true
official Top3 set is outside its discovered support, return
`J0_MAXENT_SUPPORT_BLOCKED`; otherwise complete with frozen J0 guardrail
metrics. Any LP or dual failure is fail-closed without an alternate solver.
