# P2-NANKAN-SPECIALIZED-IDENTIFIABILITY-AND-KILLTEST-PREREG-032

## Objective

Determine, without fitting a model or implementing a policy, whether the
expert-specified Nankan-specialized information structure is identifiable and
testable from strict historical as-of evidence through `2026-07-31`.

## Authoritative inputs

- `db/p2_history_context.sqlite` (read-only historical race/runner context).
- `reference/v1/db/nankan_market.sqlite` (immutable read-only historical
  `MARKET_TIME_UNKNOWN` development market authority).
- Existing frozen Phase 2 contracts, manifests, feature inventories, and 031
  closeout artifacts.
- Existing Phase 2 prospective/current/external inventories only for source
  availability classification; no post-cutoff outcome is read.

## Outputs

- `audit/reports/P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032.md`
- `audit/data/p2_nankan_specialized_identifiability_audit_032/` containing all
  task-specified CSV/JSON files and `run_manifest.json`.
- `docs/P2_NANKAN_SPECIALIZED_RESEARCH_STATUS.md`.
- A bounded deterministic audit implementation and focused tests if needed to
  reproduce the tables.

## Invariants and exclusions

- Historical cutoff is inclusive `2026-07-31`; same-day target outcomes are
  never used as prior history.
- `horses.last_seen_date` is never read.
- `reference/v1/` and all 031 artifacts remain byte-identical and read-only.
- No Web, model training, predictive-model/policy implementation, threshold or
  feature optimization, venue selection, live DB write, or production change.
- No 2026-08 or later outcome access; 2026-09-03 outcome access is zero.
- Missing/undefined scientific semantics are reported as unavailable/blocked;
  they are not filled with invented defaults.
- Historical official odds remain `MARKET_TIME_UNKNOWN`; they may establish
  development support but never masquerade as T15.
- Race date is the as-of ordering unit unless an existing authoritative
  pre-race timestamp contract proves a finer ordering. Prior observations for
  a target row therefore require `prior.race_date < target.race_date`.

## Deterministic computation contract

- Cross-venue support uses Nankan target races and stable stored horse identity;
  jockey identity uses the stored exact jockey text because no stronger local
  canonical jockey identifier is present.
- Prior cell counts are computed before each calendar-date block; multiple races
  on the same date do not update each other.
- Interaction support is emitted only for source dimensions whose categorical
  semantics are already reconstructible. Unsupported running-style or
  expected-pace dimensions remain explicit sparse/unavailable rows rather than
  invented labels.
- Condition-similarity support uses only a fixed exact-match tuple if every
  constituent category is locally authoritative; otherwise the unsupported
  dimension and resulting limitation are explicit.
- Same-day feasibility is an availability audit only. No same-day feature is
  materialized into a model dataset.
- Sensitivity and information-size tables are deterministic planning
  calculations, not outcome-guided selections.

## Failure modes and checks

1. Fail closed if a source schema or cutoff invariant differs from the expected
   contract.
2. Verify race/runner keys, join cardinality, venue set, and no post-cutoff rows.
3. Verify prior-count bins are exhaustive and same-date invariant.
4. Verify mechanical TRIO probabilities/formulas and effect/power calculations.
5. Verify every required output exists, is nonempty where applicable, and is
   cross-consistent with the report and kill-gate JSON.
6. Verify input hashes and the preserved 031 hashes after execution.

## Acceptance

- All requested sections/tables and K1--K10 decisions are present.
- WIN/TRIO authorization is derived only from demonstrably available required
  components; partial/blocked baselines cannot be silently treated as ready.
- Run manifest records `vcs_mode: none`, `git_commit: null`, hashes, versions,
  seed, commands, outputs, access counters, and zero production changes.
- Focused tests and artifact validation pass.

## Status

COMPLETE

## Completion record

- Generated every specified Markdown/CSV/JSON artifact from cutoff-bounded,
  read-only local sources.
- No model, feature, policy, threshold, venue, production, or live-DB change was
  made; post-2026-07-31 outcome access remained zero.
- Focused unit/formula/cutoff tests and independent artifact/hash/cross-total
  validation passed.
- The immutable 031 report and manifest hashes were preserved and recorded.
