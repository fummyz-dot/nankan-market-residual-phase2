# P2-M12B-R6 — NONSTARTER Historical-State Semantic Audit

## STATUS

`NONSTARTER_IDENTITY_REQUIRED_FOR_FROZEN_FEATURE_SEMANTICS`

## Scope

This was a historical implementation/state audit only.  It performed no model
training, Market-performance computation, ROI analysis, or prospective result
evaluation.

## Historical universe

Under the frozen M07 semantics, South-Kanto history through 2026-07-31 contains
4,667 `NONSTARTER` runner rows across 2,596 races.  The complete vocabulary and
year/venue profile are preserved in
`audit/data/p2_m12b_r6/historical_nonstarter_year_venue_distribution.csv`.

## Finding

V1, the Class Bradley–Terry update, Speed observations, and Pace runner
closing observations each have explicit eligibility predicates that exclude a
NONSTARTER outcome from their direct update paths.

However, frozen M03B Class feature construction records `pending_previous` for
every pre-row.  A NONSTARTER consequently changes later
`last_prior_nankan_race_*` and official-class-transition feature state.  In the
normal-versus-separated historical perturbation, the Class block had 313,363
field mismatches across 245,426 common runner rows (maximum numeric difference
1,169).  This is a frozen FS04 semantic effect.

## Decision

`P2_NONSTARTER_HISTORY_SEMANTICS_V1` was **not approved**.  In particular,
`race_nonstarter_events` separation without canonical identity was not promoted;
the 2026-08-07 Urawa 2R delta transaction remains blocked.  The pre-existing
identity contract is unchanged: an unresolved NONSTARTER cannot be converted
to a cold start or silently dropped.

## Evidence

- `audit/data/p2_m12b_r6/nonstarter_feature_lineage_audit.csv`
- `audit/data/p2_m12b_r6/nonstarter_fs04_perturbation_audit.csv`
- `audit/data/p2_m12b_r6/targeted_nonstarter_later_start_cases.csv`
- `tests/unit/test_p2_m12b_r6_nonstarter_block.py`

## Next

Stop this recovery path.  A subsequent, separately authorized source recovery
would need an approved official identity source for the affected NONSTARTER;
no identity rule was weakened here.
