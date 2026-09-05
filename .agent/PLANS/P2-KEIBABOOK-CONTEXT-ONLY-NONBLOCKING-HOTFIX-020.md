# P2-KEIBABOOK-CONTEXT-ONLY-NONBLOCKING-HOTFIX-020

## Inputs

- Existing live shadow bundle builder and Keibabook Ability/Training parsers.
- Immutable T15 materialization, frozen DEV-LIVE-V1 prediction, and Main policy.
- 2026-09-02 Ohi 7R race-day event evidence.

## Output

- Per-source CONTEXT_ONLY availability metadata in live analysis bundles.
- Missing or context-parser failures represented without blocking valid Main
  analysis and recommendation assembly.

## Invariants

- Only Keibabook context source failures are downgraded.
- Main required roster, T15, model, feature, and policy failures remain
  fail-closed.
- No model input, recommendation policy input, result, payout, or settlement
  access is added.
- Existing available Ability/Training payloads retain their sanitized form.

## Verification

1. Source-missing, malformed, available, and mixed-source bundle tests.
2. Main prediction/recommendation equality where only context availability
   changes, plus restart reuse.
3. Relevant race-shadow/race-day and leakage regressions with `compileall`.
