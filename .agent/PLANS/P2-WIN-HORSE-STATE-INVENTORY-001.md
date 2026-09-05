# P2-WIN-HORSE-STATE-INVENTORY-001 — FS04 / Horse State gap inventory

## Scope

Perform a read-only, source-backed inventory of FS04 semantic coverage and the
strict-as-of historical/live availability of Horse State / Sequence concepts.
No feature, model, architecture, or evaluation choice is made.

## Inputs

- Frozen FS04 feature manifests/registry and the source builders under
  `src/features/`.
- Phase 2 history context schema/provider and normalized historical source
  artifacts through `2026-07-31`.
- Existing frozen Class, Speed, Pace, and online history source contracts.

## Outputs

`audit/data/p2_win_horse_state_inventory_20260826/` receives the specified
feature, source, sequence-depth, concept/redundancy/parity, existing-code,
capacity, source-map, implementation, and provenance artifacts.

## Invariants and exclusions

- Inspect development history only through `2026-07-31`; no August outcome or
  result database access.
- Do not create any feature columns, fit models, calculate outcome metrics, or
  choose a candidate/architecture/search budget.
- FS04 classifications must cite code/manifest evidence.  Missing evidence is
  explicitly `UNKNOWN`, never inferred from a name alone.
- Historical-versus-live parity reflects only existing provider/contract facts;
  it is not an implementation proposal.

## Method

1. Parse exact FS04 registry and trace each source family to its builder and
   source artifact.
2. Inspect normalized historical provider/schema and existing online overlay
   interfaces for strict-as-of source-field availability.
3. Compute past-race sequence depth from target-date-before-only history,
   without opening result/outcome databases or using target outcomes.
4. Mechanically map the registered concepts to documented FS04 feature names,
   source fields, missingness coverage, and parity facts.
5. Search existing code for prior sequence/state implementations and record
   their status without reuse.

## Failure handling

- Any required source/manifest/schema ambiguity is recorded as `UNKNOWN`,
  `DATA_NOT_READY`, or `BLOCKED`; it is never resolved by a guessed semantic.
- The audit writes atomic artifacts and a `vcs_mode:none` run manifest.

## Acceptance

- All requested artifacts exist, development capacity/depth are source-backed,
  and hard audits report zero model fit, August/result DB access, feature
  implementation, production-code change, and production DB mutation.
