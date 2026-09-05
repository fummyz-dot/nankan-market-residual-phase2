# JOB007R2 implementation plan

- Inputs: frozen Stage2 authorities, Job003B v1.1 materialized features, Job004 Fold4 artifacts, and (only after Phase A) local prospective market/live-history databases.
- Outputs: provider-injected forward adapter, exact frozen scorer, blinded prequential primitives, guarded Phase-A parity audit, local replay artifacts, and aggregate tracked evidence.
- Invariants: no Phase-A access to the frozen denylist; exact 129/32 feature order; fixed M2/race-head/EB lineage; history date strictly before target date; immutable prediction-before-result barrier; no performance aggregates.
- Exclusions: network collection, payout access, legacy178, retraining, same-day state updates, formal Stage2 evaluation, ROI/profit.
- State transitions: source/tests -> implementation commit -> guarded historical parity -> commit-bound `PHASE_A_PASSED.json` -> Phase-B local replay -> blinded evidence commit/push.
- Failure handling: denylist attempt, authority/hash mismatch, parity mismatch, immutable conflict, reconciliation mismatch, or incomplete EB update fails closed without Phase-B fallback.
- Acceptance: the JOB007R2 gates and support-only reporting specified by the task.
