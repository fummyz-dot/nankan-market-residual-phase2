# P2 Recommendation Evidence V1 Contract

`P2_RECOMMENDATION_EVIDENCE_V1` is the immutable audit evidence that a
pre-race `race-shadow` bundle generated a fixed, versioned-policy recommendation. It
is neither a prediction freeze, an order, an actual purchase, nor settlement.

The evidence schema remains V1 while the retained policy is versioned. Legacy
day plans may retain `P2_OPS_BET_POLICY_V1`; new day plans use
`P2_OPS_BET_POLICY_V2`, whose Main recommendation accepts WIN tickets only.
Under V2, WIDE is explicitly `DISABLED_RESEARCH_ONLY` and is not a Main ticket,
stake, or partial-scope failure. Its independent research evidence remains
outside this ledger.

## Normal live ordering

1. `race-shadow` materializes the existing pre-race inputs and emits the
   existing analysis bundle to a temporary file.
2. The final bundle file is fsynced and atomically renamed. Its stored-file
   SHA-256 is calculated.
3. The writer validates the retained `recommendation` block without
   recalculating model probabilities, WIDE probabilities, or policy thresholds.
4. In one `PRAGMA foreign_keys=ON` ledger transaction it resolves the existing
   canonical race parent by natural key, inserts `recommendation_records` and
   `recommendation_tickets`, and validates ticket count/stake invariants.
5. Only then may `race-shadow` render `ANALYSIS_READY`.

The bundle's canonical-content hash remains unchanged. The ledger stores the
final bundle-byte hash, so no circular bundle/evidence hash exists.

## Identity and immutability

The deterministic ID is `P2_REC_V1::<sha256>` over the canonical evidence
payload: race key, final bundle hash, model identity, policy identity,
predecision-reference identifiers, and the retained recommendation block.
There is one operational recommendation per canonical race parent.

A same-input retry is `RECOMMENDATION_EVIDENCE_IDEMPOTENT`. A different
recommendation for the same race is
`RECOMMENDATION_ALREADY_COMMITTED_DIFFERENT` and fails closed. SQLite triggers
prevent updates/deletes of committed recommendation records and tickets.

## Scope and boundaries

The writer accepts only `LIVE_SHADOW` bundles with
`prediction_info.freeze_status=NOT_REQUIRED_RECOMMENDATION_EVIDENCE`, an
offset-aware pre-race reference (`T15_STANDARD` or `PRE_RACE_FALLBACK`), and
`source_boundary.result_db_accessed=0`.

It validates only the existing bundle decision: BET/NO_BET, stake totals,
active roster selections, canonical ticket shapes permitted by the retained
policy, and fixed model/policy hashes. It never reads or writes `actual_bets`, result captures,
runner results, payouts, reconciliation, or outcome data.

`official_result_collector` remains independent: a result may be collected for
a naturally keyed race with no recommendation evidence.

## Legacy records

Legacy prediction/Decision freeze commands and their data remain available for
diagnostic compatibility. They are not required by the normal `race-shadow`
path. The first `ANALYSIS_READY` recommendation is the official operational
recommendation; later normal reruns return its committed evidence rather than
silently selecting a newer market snapshot.
