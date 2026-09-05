# P2-WIDE-PROSPECTIVE-SHADOW-LIVE-001 — prospective WIDE research shadow

## Job metadata

- Status: COMPLETE
- Scope: connect the immutable `wide_prospective_v1` research bundle to the
  existing race-day pre-race snapshot path and a separate post-race evaluator.

## Inputs and durable outputs

- Inputs: frozen `models/development/wide_prospective_v1/` bundle, existing
  T15/fallback capture-set resolver, active roster/materialized FS04 values,
  and official post-race result semantics.
- Outputs: an independent `P2_WIDE_RESEARCH_EVIDENCE_V1` ledger, immutable
  research payloads, separate prospective prediction/evaluation artifacts,
  and a cumulative confirmation manifest.

## Invariants and exclusions

- Main `race-shadow` recommendation, Recommendation Evidence, policy, stake,
  and output are authoritative and remain unchanged. Research begins only
  after main evidence is committed and never blocks it.
- Research accepts only actual pre-race, immutable T15 or fallback capture
  sets, verifies frozen bundle hashes, and never accesses result/payout data
  before the race-day PRE_RACE barrier opens.
- No prediction is backfilled after post time. Research evidence is separate
  from recommendation/actual-bet/result foreign-key dependencies and is
  idempotent by race and frozen model bundle hash.
- J0/J1 are full-support joint research models; PL is retained only as the
  explicitly-labelled engineering benchmark. None becomes recommendation or
  stake input.

## State / transaction boundaries

1. Verify the frozen artifact manifest and resolve an existing valid main
   predecision capture-set.
2. Before post time, construct Market/J0/D1/J1/PL research payload and write
   payload file plus a single research-evidence transaction.  A failed or
   invalid research path records a research-only terminal state.
3. On restart, reuse existing evidence; retry only while pre-race.  After post
   time without evidence, record `RESEARCH_PREDICTION_MISSED` and do not score.
4. After the existing PRE_RACE barrier only, resolve official outcomes and
   evaluate committed research evidence, keeping T15 and fallback aggregates
   separate.

## Acceptance / failure coverage

- T15, fallback, incomplete WIDE, model-hash mismatch, invalid joint,
  idempotency, pre/post restart, main-isolation, and no-pre-race-result-access
  coverage.
- Post-race Pair CE, Set NLL, binary/Brier and cumulative-ledger idempotency;
  special/unknown WIDE outcome semantics fail closed.
- Fresh-process temporary DB smoke and 11/12/14-runner runtime measurement.

## Completion

- Frozen artifact SHA-256 verification, separate immutable research evidence,
  research-only failure states, and post-race evaluator are implemented.
- `race-day` starts the supervised research child only after Main Evidence and
  `ANALYSIS_READY`; it never waits for the child before the user-facing main
  recommendation, and stops/marks a missing prediction at the pre-race
  barrier.
- Targeted unit/integration/fresh-process tests, no-result pre-race gate, and
  11/12/14-runner runtime measurements are recorded in
  `audit/data/p2_wide_prospective_live_v1_20260826/`.
