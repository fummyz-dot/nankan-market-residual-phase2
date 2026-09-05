# P2-LIVE-OPS-V2-DESIGN

## Objective

Define, without changing production code, the Live Operations V2 user workflow and managed implementation backlog.  The target is a one-command race-day operation plus one minimal decision command, while retaining the already-working internal modules as maintenance interfaces.

## Inputs

- `docs/USER_OPERATION_CONTRACT.md`
- Live-ledger, official-result, and reconciliation contracts
- Existing prospective collector, `race_shadow`, current-info, and result-collector interfaces
- 2026-08-21 prospective operational incidents and their retained regressions

## Outputs

- `docs/P2_LIVE_OPERATIONS_V2_DESIGN.md`
- `docs/P2_LIVE_OPERATIONS_V2_TODO.md`

## Invariants

- This job is documentation/design only: no production source, database, CLI, collector, prediction, decision, or result-path changes.
- Existing 2026-08-21 identity and natural-race-key recoveries remain retained regressions, not redesign work.
- Pre-race operations remain result-DB independent.
- Missing Keibabook context is visible but does not block the fixed FS04 model.
- Recommended-strategy accounting is derived from the frozen recommended portfolio, never actual user purchases.

## Acceptance

- The normal user actions, hidden internal steps, static/dynamic gate split, state/exit semantics, staged result completeness, and implementation phases are explicit.
- Every planned item has ID, priority, status, problem, design requirement, acceptance, dependencies, and estimated implementation depth.
- Today’s Funabashi operation remains on the existing pipeline.

## Exclusions

- No implementation, migrations, refetches, model retraining/search, performance evaluation, or ROI evaluation.
