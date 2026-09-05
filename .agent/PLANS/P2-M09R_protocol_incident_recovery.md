# P2-M09R — Protocol Incident Recovery & Outer-Validation Integrity Audit

## Inputs

- `audit/data/p2_m09/PRE_PERFORMANCE_PROTOCOL_INCIDENT.md`
- M08B manifest, backend/grid/walk-forward/selection configs, FS00 registry
- Existing M09 output/checkpoint/model paths, inspected read-only

## Invariants

- Do not train LightGBM, calculate loss, run a configuration, bootstrap, or inspect outer-validation performance.
- Preserve `P2-INC-001`; formal budget remains `0/6` and incidental peek count remains `1`.
- May/June/July outer validation must have no formal artifact.
- Frozen M08B config/objective/FS00 hashes must reconcile.

## Completed steps

1. Bounded the incident to the documented March-to-April inner two-tree probe.
2. Audited absence of formal M09 outer outputs, selected model, checkpoints, and budget consumption.
3. Reconciled frozen configuration/feature/objective hashes and documented the M09-specific pre-incident implementation extension.
4. Added an explicit formal-real-evaluation guard and read-only assertions.
5. Wrote M09R audit, manifest, report, recovery config, and persistent wording amendments.
