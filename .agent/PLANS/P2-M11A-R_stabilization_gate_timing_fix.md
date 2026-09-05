# P2-M11A-R — Stabilization Gate Amendment & Pre-Decision Capture Timing Fix

## Inputs

- Existing M11A current-info snapshots, collector, dashboard, and retained Kawasaki fixture.
- `P2_STABILIZATION_GATE_V1`, source/candidate contracts, and H2 budget registry.

## Outputs

- Retained/superseded gate V1 plus active `P2_STABILIZATION_GATE_V2`.
- Collector request-lead and predecision-only retry semantics.
- T15 timing status persisted as `PREDECISION_VALID`, `LATE_AFTER_DECISION`, or `STALE_FOR_T15`.
- Dashboard, audit, manifest, report, contract and decision updates.

## Invariants

- No outcome, performance, payout, ROI, or feature-selection access.
- H2-C05 remains unevaluated; H2-C06 remains unallocated.
- Late T15 raw capture is retained but cannot prove availability or increment coverage.
- Existing fixture bytes/artifacts remain unchanged.

## Acceptance

- 14-day/80-race/four-venue/10-per-venue gate is active.
- Valid window is `[decision_time - 60 sec, decision_time]`.
- Initial T15 request lead is 30 seconds and retry is predecision-only.
- Tests/audits prove late/stale exclusion, budget/outcome firewall, and fixture honesty.

## Status

Completed pending deterministic test and audit closeout.
