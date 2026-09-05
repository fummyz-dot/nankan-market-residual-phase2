# P2-M07 — Target Universe & Market-Offset Foundation

## Inputs

- Read-only P2 historical context DB, M02 class-rule artifact, M06 feature matrix and metadata.
- Existing Class/Speed/Pace contracts and frozen M06 feature-set registry.

## Outputs

- Development-frozen pre-race race universe and separately stored runner outcome semantics.
- Frozen outcome-status, target-universe, and market-offset model-foundation configurations.
- Deterministic manifests, audits, tests, contracts, and report.

## Invariants

- Race eligibility reads only race identity and pre-race class/taxonomy semantics.
- Outcome handling never changes race eligibility; Market and Keibabook are never opened.
- M06 matrix remains immutable, label-free, and `HISTORICAL_DEVELOPMENT_ROSTER`.
- No model training, probability evaluation, odds access, ROI, or backend search occurs.

## Acceptance

- Each of 21,849 races gets exactly one frozen status with no review/unresolved status.
- All 250,093 runners receive explicit outcome semantics; unresolved WIN labels are visible rather than silently removed.
- WIN soft dead-heat targets conserve unit race mass when usable.
- FS00–FS04 and race-offset loss form are recorded without fitting parameters.
