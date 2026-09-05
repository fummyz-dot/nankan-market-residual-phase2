# P2-RESUME-RESEARCH-PROVENANCE-HOTFIX-022

## Scope

Unify the public idempotent return payloads of the existing WIN and CURRENT
research sidecars with their committed-prediction provenance shape.  This is
a read-only projection of immutable evidence; no evidence, schema, model,
policy, or race-day lookup contract changes.

## Inputs

- `win_research_evidence` / `current_research_evidence` immutable rows
- Existing deterministic `_prediction_path()` contracts

## Invariants

- Provenance comes only from the persisted row and deterministic artifact path.
- Missing durable provenance fails closed; it is never synthesized from Main.
- Evidence IDs, payload hashes, prediction data, Main evidence, and DB schema
  remain unchanged.
- Generic `.COMPLETE.json` remains lifecycle-only.

## Acceptance checks

1. WIN and CURRENT commit/idempotent outputs agree on provenance fields.
2. Race-day renders non-null provenance from idempotent child output.
3. Missing durable provenance cannot become a READY result.
4. Relevant unit/integration tests and `compileall` pass.
