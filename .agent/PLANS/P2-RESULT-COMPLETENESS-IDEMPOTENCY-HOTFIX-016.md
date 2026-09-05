# P2-RESULT-COMPLETENESS-IDEMPOTENCY-HOTFIX-016

## Inputs

- `result_completeness_evidence` append-only rows keyed by
  `(race_key, raw_sha256)`.
- `official_result_collector.persist_result_completeness()` and its existing
  semantic assessment fields.

## Output

- A semantic-only `assessment_payload_sha256` that excludes acquisition
  metadata while retaining first-write source-reference provenance.

## Invariants and exclusions

- No database repair, table mutation, migration, or production-data write.
- Existing append-only table, unique key, and immutable update/delete triggers
  remain unchanged.
- An identical raw SHA with changed semantic state or reason remains a
  fail-closed conflict.
- Result finality, settlement, science, and policy semantics are unchanged.

## Verification

1. Same raw/semantic assessment with changed timestamp/archive path is a
   committed row followed by `IDEMPOTENT_NOOP`.
2. Changed history or payout/reason semantics for the same raw fail closed.
3. A new raw SHA remains append-only; immutability triggers still reject
   update/delete.
