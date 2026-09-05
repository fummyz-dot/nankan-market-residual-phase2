# P2-WIDE-OHI-T15-PRICE-CONVERSION-SHADOW-V0-001

## Scope

Add an outcome-blind, research-only Ohi sidecar that freezes one existing
WIDE-P0 selection from the committed T15 WIDE J1 evidence, then observes only
that pair in the collector's existing T10/T05 WIDE captures.  It does not
change Main, Funabashi Shadow/Experimental, J1, P0, or any purchase path.

## Inputs

- Immutable Main Recommendation Evidence with `T15_STANDARD` and a Primary
  scientific sample.
- Existing committed frozen WIDE J1 payload in `wide_research_evidence`.
- Existing explicit `P2_MKT_ONLY` T10/T05 WIDE captures and complete pair
  snapshots from the prospective collector.

## Invariants

- Venue is only `大井`; selection is frozen WIDE-P0 (one maximum positive
  `ln(q_j1/q_market)` pair with `10 <= lower_odds < 20`).
- Market/J1 are validated in normalized q scale (race mass 1).  Later Market
  q uses the already-existing WIDE M0 lower-odds normalization and frozen
  market gamma; later J1 is never inferred.
- T15 selection is immutable.  T10/T05 only observe the same canonical pair;
  missing/nonmatching marks are research-only `TRAJECTORY_INCOMPLETE`.
- The price-support gate is outcome blind and freezes the chronological first
  three valid trajectories.  Its artifacts never read results, payouts, ROI,
  settlement, or `actual_bets`.
- JSON evidence lives only in
  `outputs/live_development/wide_ohi_price_shadow_v0/`; conflicts fail closed
  for this sidecar without blocking Main.

## Validation

Synthetic tests cover eligibility, boundaries, fixed-pair trajectory,
incomplete captures, first-three gate, no-result boundary, idempotency,
conflict isolation, and race-day/Main/Funabashi isolation.  Run targeted
WIDE/race-day suites and `compileall`.
