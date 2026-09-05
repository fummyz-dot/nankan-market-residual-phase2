# P2-CURRENT-STABILIZATION-METRICS-V2-HOTFIX-028

## Objective

Correct P2_CURRENT readiness reporting so CUR03 is derived only from immutable
`P2_CURRENT_JOCKEY_CONTEXT_V2` evidence, while preserving V1 as separately
labelled historical provenance.

## Inputs

- Existing prospective CURRENT snapshot tables for CUR01/CUR02/CUR06.
- Immutable `current_prospective_v1` and `current_prospective_v2` evidence
  artifacts.
- Existing collector checkpoint evidence, only to label failed bodyweight raw
  as regression-only rather than committed coverage.

## Invariants

- No V1/V2 jockey-context aggregation.
- CUR03 values are only SAME/CHANGED; NO_PRIOR_START is null-by-design and
  UNKNOWN is unresolved.
- CUR04/CUR05 remain unimplemented and CUR06 remains snapshot-roster based.
- No model, feature, policy, capture, race-day, or database-schema change.

## Validation

- Focused synthetic tests cover V1/V2 separation, CUR03 status/reason counts,
  bodyweight null semantics, artifact validation, venue observation, and the
  non-automatic H2-C05 re-audit gate.
- Run focused existing CURRENT parser/identity regressions and compileall with
  the production virtual environment.
