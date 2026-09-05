# P2-M03B — Strict-As-Of Empirical Class Strength Feature Build

## Scope

Rebuild the frozen `P2_CLASS_EMPIRICAL_MAIN_V1` rating (`R3`, `K=1.00`) from
the read-only M01 DB, then produce South-Kanto runner and race empirical-class
datasets. This is feature construction only: no Market, odds, ROI, model, or
residual evaluation source is opened.

## Inputs

- `db/p2_history_context.sqlite` (read-only)
- `data/curated/p2_class_rule/nankan_race_class_rule.csv.gz`
- M03A selected configuration and prototype, used only to audit rebuild parity

## Frozen invariants

- Read `K=1.00`, date-block timing, other-flat prohibition, and exchange-update
  prohibition from the selected configuration; reject any different value.
- Rebuild pre-ratings from state, never copy the M03A prototype as output.
- Context observations use only a past race's pre-race rated-runner mean.
- A rated runner has `rating_prior_nankan_races > 0`; cold-start zero never
  enters the observed field mean.
- Previous-race state and context state update only after every race on the
  current calendar date has been output.
- Other-flat results never update ratings; exchange races receive pre-race rows
  but do not update state.

## Outputs and acceptance

- Complete 250,093-runner and 21,849-race deterministic curated outputs.
- M03A rating parity, date-block, prohibited-source, exchange, and other-flat
  audits pass.
- Canonical logical hashes match a second independent rebuild.
- The feature contract documents formulas, missingness, and non-CI information
  semantics without adding ablation candidates.

## Completion

Completed foreground on 2026-08-19 with frozen `R3/K=1.00`. The formal
runner/race outputs rebuild M03A pre-ratings with zero parity mismatch and
match on a second logical rebuild. No Market source was used.
