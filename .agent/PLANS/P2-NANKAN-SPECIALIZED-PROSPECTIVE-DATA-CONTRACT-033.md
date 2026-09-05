# P2-NANKAN-SPECIALIZED-PROSPECTIVE-DATA-CONTRACT-033

## Inputs

- Frozen task 033 and `DESIGN-AUTHORITY-SUPPLEMENT-V1`.
- Existing official T15/CURRENT capture contracts and storage.
- Synthetic authorities only for validation; no outcome-based analysis.

## Outputs

- Frozen collection contract/manifest, raw-authority SQLite ledger, collection-only command,
  replay/quality status, tests, implementation audit, and updated specialized status.

## Invariants

- No model, selection, policy, or Actual-bet path.
- P0/P1/P2 T15 capture is never blocked by P4 same-day polling.
- Raw authority is append-only; day plans and schedule revisions are never overwritten.
- T15 is valid only in the inclusive `D-60s .. D` window.
- Same-day results enter only when first seen by the target decision time.

## Failure handling

- Parser/collector/source-conflict conditions make a race-day incomplete.
- Structural/as-of same-day states remain valid and distinct from collection failure.
- A late result, market capture, or schedule-revision conflict is retained but cannot be promoted.

## Acceptance

- Synthetic tests cover every task-required boundary and no-bet/passive-market firewall.
- Deterministic replay gives byte-identical canonical outputs.
- Contract and implementation reports record cohort, quality, 20/40/80/160/240 rules, and
  no production-model/policy change.
