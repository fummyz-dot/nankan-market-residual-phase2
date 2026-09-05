# P2-MIDDLE-ODDS-SCIENTIFIC-HEADROOM-AUDIT-029

## Objective

Read-only scientific diagnosis of the fixed middle-odds WIN/WIDE thesis after
AUDIT-028.  Determine whether the observed loss of opportunity is attributable
to market structure, the frozen probability models, unused pre-race information,
or insufficient data.  This is not a model, policy, threshold, or parameter
search.

## Inputs

- Canonical strict OOF WIN authority:
  `audit/data/p2_win_residual_shrinkage_20260826/oof_predictions.parquet`
  plus its frozen H2-C04 provenance and official historical odds.
- Canonical strict outer-OOF WIDE authority:
  `audit/data/p2_wide_j1_d1_joint_20260825/j1_outer_predictions.parquet`
  joined to `audit/data/p2_wide_sci_baseline_20260825/fold_predictions.parquet`.
- AUDIT-027/028 frozen audit artifacts for already bounded prospective evidence.
- Local contracts under `docs/` for the unused-information inventory.

## Invariants and exclusions

- Historical outcomes and OOF rows are restricted to `race_date <= 2026-07-31`.
- No 2026-08/09 outcome source is opened; 2026-09-03 outcome access is zero.
- Frozen definitions only: WIN odds 8--25 / P floors .10/.15/.20; WIDE lower
  odds 10--20 / p_j1_hit >= .15.
- Calendar-date clustered bootstrap, seed `20260903`, 10,000 resamples.
- All four venues are diagnostic-only and identically tabulated.
- Permitted writes are this plan and audit artifacts only.  The audit program is
  a temporary `/tmp` script; no production source, model, policy, or DB changes.

## Outputs

- `audit/reports/P2_MIDDLE_ODDS_SCIENTIFIC_HEADROOM_AUDIT_029.md`
- `audit/data/p2_middle_odds_scientific_headroom_audit_029/` including the
  required machine-readable tables and run manifest.

## Acceptance checks

1. Verify the canonical sources, temporal span, and fold identifiers before
   computing any metric.
2. Emit frontiers, clustered empirical frontiers, residual/required-residual
   distributions, high-P price locations, GER necessary-condition tables,
   target-band headroom diagnostics, failure decomposition, and information
   inventory.
3. Validate output counts, cutoff, manifest boundaries, and artifact hashes.
4. Record no DB writes and no production-source/model/policy changes.

## Status

COMPLETE

## Completion record

- Revalidated WIN and WIDE canonical OOF identity, fold timing, and cutoff.
- Wrote the required report, machine-readable frontiers, residual/GER/headroom
  tables, unused-information inventory, summary, and provenance manifest.
- Validation reruns compile and execute the temporary read-only audit program;
  output/date/manifest boundary checks are recorded in the final artifact.
