# P2-WIDE-SCI-BASELINE-001 — Calibrated WIDE Market + OOF PL benchmark

## Objective

Using development-period data only, compare the fixed three historical WIDE
market normalizations under fold-safe gamma calibration, derive exact
Plackett–Luce WIDE pair mass from H2-C04 OOF-safe WIN predictions, and run the
registered one-parameter Market+PL benchmark.  This is probability research
only: historical market timestamps remain `MARKET_TIME_UNKNOWN` and no
economic claim is made.

## Inputs

- `reference/v1/db/nankan_market.sqlite` — historical WIDE odds and official
  payout-pair labels, read-only.
- `data/curated/p2_model/win/h2/h2_nar_core_outer_runner_predictions_v1.csv.gz`
  — H2-C04 outer validation predictions only.
- `audit/data/p2_m08b/walkforward_fold_manifest.csv` — unchanged WF1–WF3
  boundaries.
- `data/curated/p2_target/nankan_race_target_universe_v1.csv.gz` — canonical
  race-key crosswalk.
- `audit/data/p2_wide_science_inventory_20260825/` — verified 481-race,
  29,136-pair development intersection and OOF contract.

## Outputs

`audit/data/p2_wide_sci_baseline_20260825/`:

- three candidate market results and frozen selected-market manifest;
- PL-only and Market+PL OOF benchmark artifacts;
- pair-level `fold_predictions.parquet`;
- normalization, bootstrap, search-budget, implementation, and run manifests.

## Invariants

- Only development dates through `2026-07-31`; no August outcome source is
  opened.
- Candidate market `q` and scientific PL `q_PL=p_hit/3` sum to one per race;
  PL hit mass sums to three.
- Payout labels must contain exactly three canonical winning WIDE pairs for
  every 481-race comparison row.  Any special outcome blocks the task.
- Market gamma fit uses only dates before each target fold's validation start.
- Joint beta fit uses only prior OOF-safe validation races; unavailable folds
  are recorded and excluded from the joint comparison.
- No model fit, feature change, live-policy change, production DB mutation,
  payout/ROI/economic evaluation, or result use after 2026-07-31.

## Tests

Unit tests cover M0/M1/M2 formulas, gamma identity/bounds and leakage
selection, PL probability invariants and shuffle invariance, beta=0 identity,
determinism, V1 pair-CE reproduction, common-set equality, and development
date firewall.  The executable additionally performs all real-data hard
audits and records their values.

## Acceptance

- 481 OOF-safe roster-exact common races and 29,136 canonical pairs are
  re-established from inputs.
- One candidate is selected only from the three fixed calibrated candidates.
- PL and joint OOF benchmarks, fold-safe calibration records, calendar-date
  bootstrap, search budget, provenance, and required artifacts are saved.
- All hard normalization/leakage/source-immutability audits pass.
