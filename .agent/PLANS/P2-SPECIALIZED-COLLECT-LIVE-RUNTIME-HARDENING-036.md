# P2-SPECIALIZED-COLLECT-LIVE-RUNTIME-HARDENING-036

## Inputs

- Frozen 033 collection contract and SHA-256 manifest.
- Frozen 036 runtime, locking, immutable-commit, P4, and exit semantics.
- Existing specialized collection ledger and official-only adapter.

## Outputs

- Collection runtime with a no-argument supervisor, kernel single-writer lock,
  append-only event ledger, immutable race artifacts, recovery, isolated P4,
  and bounded exit classes.
- Deterministic subprocess E2E and fault-injection tests plus 036 audit files.

## Invariants

- No model, policy, outcome evaluation, recommendation, or purchase import.
- T15 raw authority is immutable after a matching commit ledger event.
- P4 cannot write the plan, canonical T15 artifacts, or day manifest.
- Normal operator entry is only `./specialized-collect`.

## Acceptance

- Execute the actual launcher against a 12-race accelerated fixture, crash/
  resume, lock contention, P4 priority, all frozen fault cases, and fresh-shell
  commands.  Record hashes and exit classes.
