# P2-WIDE-FUNABASHI-SHADOW-V0-001

## Scope

Integrate the already-frozen prospective WIDE J1 artifact into race-day as a
research-only Funabashi WIDE-P0 Shadow.  Main recommendations, actual bets,
the frozen WIDE bundle, and all rejected selector/gate artifacts remain
outside this change.

## Inputs

- Immutable Main Recommendation Evidence and its `predecision_reference`.
- Existing `P2_WIDE_PROSPECTIVE_FREEZE_V1` research prediction payload.
- Exact T15 WIDE lower/upper odds retained in that payload.

## Invariants

- Gate is only `venue == 船橋`; Primary, `T15_STANDARD`, scientific sample,
  complete WIDE market, active roster, and same-scale Market/J1 are required.
- WIDE-P0 selects at most one pair: `10 <= lower_odds < 20` and
  `ln(q_j1/q_market) > 0`, with the specified deterministic tie-break.
- Evidence is immutable JSON under `outputs/live_development/wide_shadow_v0/`.
  Same semantic retry is an idempotent no-op; a differing retry is a
  research-only conflict.  No database table or `actual_bets` write is added.
- Main processing and PRE_RACE_FALLBACK remain non-blocked and unmodified.

## Validation

- Synthetic unit coverage for P0 boundaries, tie-breaks, race eligibility,
  incomplete input, idempotency, and separate post-race evaluation.
- Existing race-day and prospective WIDE tests, plus `compileall`.
