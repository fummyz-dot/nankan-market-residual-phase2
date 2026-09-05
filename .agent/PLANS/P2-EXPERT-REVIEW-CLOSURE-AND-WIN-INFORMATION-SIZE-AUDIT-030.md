# P2-EXPERT-REVIEW-CLOSURE-AND-WIN-INFORMATION-SIZE-AUDIT-030

## Objective

Apply the supplied expert decision without changing models or policy: complete
the final WIDE probability-scale/coverage closure and determine whether the
single retained WIN `.10 × odds 8--25` future experiment can collect useful
confirmatory information on a realistic calendar horizon.

## Inputs

- Frozen strict OOF WIN and WIDE authorities used by AUDIT-027--029.
- AUDIT-027 production audit script, temporary AUDIT-028/029 scripts, and their
  immutable reports/artifacts for scale-use inspection only.
- Read-only prospective committed T15 evidence plus retained pre-race market
  captures through 2026-09-03; no evaluations, official payouts, results, or
  outcome columns are queried.

## Invariants and exclusions

- Historical labels are limited to 2026-07-31 and strict frozen OOF rows.
- 2026-08 onward is coverage-only; 2026-09-03 outcome access is zero.
- WIDE fixed scope is `p_j1_hit >= .15`, lower odds 10--20, all venues pooled.
- WIN fixed scope is `P(win)>=.10`, odds 8--25, all venues pooled.
- IID reference alternatives are fixed at .12/.15/.18; alpha=.05 one-sided,
  power=.80. Date-block simulation uses seed 20260903.
- No training, production source/policy/threshold/architecture changes or DB
  writes.  Only audit report/data and this plan may be written; audit program
  lives temporarily under `/tmp`.

## Outputs

- `audit/reports/P2_EXPERT_REVIEW_CLOSURE_AND_WIN_INFORMATION_SIZE_AUDIT_030.md`
- `audit/data/p2_expert_review_closure_and_win_information_size_audit_030/`
  including `wide_scale_audit.json`, `win_occurrence_rate.csv`,
  `information_size.csv`, `horizon_projection.csv`, and run manifest.

## Acceptance checks

1. Verify normalized WIDE mass sums by race and all factor-of-three identities.
2. Inspect 027--029 source/artifact formulas and independently compare previous
   versus correctly scaled `.15 / p_market_hit` values.
3. Reproduce WIDE OOF and exact T15 coverage without reading prospective
   outcomes; calculate WIN historical/prospective coverage and information size.
4. Validate cutoff, no-outcome query contract, output hashes, and zero writes.

## Status

COMPLETE

## Completion record

- Revalidated all frozen WIDE mass/probability factor-of-three contracts and
  confirmed scale use in AUDIT-027, 028, and 029 source/artifact evidence.
- Generated the requested WIDE closure, WIN occurrence, exact IID information
  size, date-block diagnostic, horizon, and economic-precision artifacts.
- Validation recompiles/reruns the temporary read-only audit program and checks
  all output/input hashes, cutoffs, no-outcome access, and no-write boundaries.
