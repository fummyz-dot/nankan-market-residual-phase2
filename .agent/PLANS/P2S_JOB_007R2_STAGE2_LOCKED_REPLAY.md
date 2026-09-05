# JOB007R2 implementation plan

- Inputs: frozen Stage2 authorities, Job003B v1.1 materialized features, Job004 Fold4 artifacts, and (only after Phase A) local prospective market/live-history databases.
- Outputs: provider-injected forward adapter, exact frozen scorer, blinded prequential primitives, guarded Phase-A parity audit, local replay artifacts, and aggregate tracked evidence.
- Invariants: no Phase-A access to the frozen denylist; exact 129/32 feature order; fixed M2/race-head/EB lineage; history date strictly before target date; immutable prediction-before-result barrier; no performance aggregates.
- Exclusions: network collection, payout access, legacy178, retraining, same-day state updates, formal Stage2 evaluation, ROI/profit.
- State transitions: source/tests -> implementation commit -> guarded historical parity -> commit-bound `PHASE_A_PASSED.json` -> Phase-B local replay -> blinded evidence commit/push.
- Failure handling: denylist attempt, authority/hash mismatch, parity mismatch, immutable conflict, reconciliation mismatch, or incomplete EB update fails closed without Phase-B fallback.
- Acceptance: the JOB007R2 gates and support-only reporting specified by the task.

## JOB007R3 source-semantics continuation

- Inputs: exact packaged Primary129 target-source authority and retained pre-race card archives only during source audit.
- Outputs: official-card prize/jockey-affiliation parsers, exact feature encoders, source-coverage audits, then the existing locked replay if coverage passes.
- Invariants: same-row official jockey binding; race-level ordinal prize binding; exact Decimal yen conversion; no result/payout access during Phase S; no inferred fallback.
- State transitions: authority/parser/tests -> source-semantics commit -> fresh guarded Phase A -> pre-race-only Phase S -> Phase B only on full source coverage.
- Failure handling: any ambiguous or missing pre-race semantic blocks before result access and before replay.
- Phase-B implementation: rebuild the Job003/003B feature state and Job004 Fold4 EB residual ledger through 2026-07-31; for each retained post-cutoff meeting date, freeze all T15 predictions (or deterministic pre-outcome exclusions), then reconcile outcomes and append every South-Kanto actual-starter residual for the next date.
- Atomicity/idempotency: prediction, date-freeze, and reconciliation JSON files are immutable-content writes; audit CSV/JSON and the mutable EB ledger use temporary-file replacement; any immutable conflict or incomplete meeting-date state update blocks the replay.
- Time boundary: all target features, EB effects, and calibration parameters for date d use dates strictly less than d; all date-d model artifacts freeze before any date-d result query; timezone-dependent decision timestamps are preserved from the stored T15 snapshot and are not recomputed.
