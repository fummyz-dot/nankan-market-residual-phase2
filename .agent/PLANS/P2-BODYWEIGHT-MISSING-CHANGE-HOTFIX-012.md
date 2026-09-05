# P2-BODYWEIGHT-MISSING-CHANGE-HOTFIX-012

## Inputs

- Retained 2026-09-01 Ohi 3R/4R official current-info raw captures whose
  bodyweight cells contain an absolute integer plus the exact `-` placeholder.
- Existing `parse_bodyweight()` numeric signed-change parsing and nullable
  `current_runner_info.body_weight_change_kg` storage contract.

## Change

Recognize only the evidenced exact `NNN -` bodyweight form when the numeric
signed-change form does not match. Preserve the absolute weight and emit
`body_weight_change: None`; leave all existing numeric parsing unchanged.

## Invariants and exclusions

- The output key remains present for every successfully parsed runner.
- `-` is not mapped to numeric zero; malformed/missing absolute weights,
  duplicate identities, and roster/count mismatches remain fail-closed.
- No schema, model, Main, current-research semantics, collector lifecycle,
  historical checkpoint, production DB, or outcome data change.
- Retained 3R/4R raws are read-only validation inputs only.

## Acceptance and validation

- Add focused parser, NULL persistence, and current-research missing-change
  regressions; retain existing numeric and roster fail-closed coverage.
- Parse retained 3R/4R raw files read-only as six active runners each with
  `None` change.
- Run the required production-venv parser/current/collector/race-day/model
  tests and compileall; record audit provenance without production DB writes.
