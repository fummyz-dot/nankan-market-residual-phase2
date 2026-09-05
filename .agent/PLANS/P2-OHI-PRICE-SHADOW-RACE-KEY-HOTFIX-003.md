# P2-OHI-PRICE-SHADOW-RACE-KEY-HOTFIX-003

## Objective

Correct only the Ohi Price Shadow cross-namespace race-key comparison and
the incomplete-trajectory top-level status reporting defect, then
deterministically re-materialize 2026-09-01 Ohi 7R from its frozen T15
selection and retained pre-race T10/T05 WIDE captures.

## Inputs

- `src/operations/wide_ohi_t15_price_conversion_shadow_v0.py`
- Existing immutable 7R T15 selection and retained WIDE capture IDs
  `9dc90cbe-0755-4bf6-a8b7-06e79ad8608e` / `cbcf4f95-69ab-4bc7-a35d-f937cd9d09ca`
- `db/market_snapshot.sqlite` (read-only)

## Invariants and exclusions

- Exact natural-key identity only; no canonical-key migration, aliases, or
  fuzzy normalization.
- Frozen T15 pair, marks, gamma, q construction, completeness, roster, and
  first-three logic are unchanged.
- No result, payout, settlement, collector, network, or production-DB write.
- Recovery may write only the authorized 7R research JSON/state and hotfix
  audit artifacts.

## Acceptance and validation

- Focused identity, lifecycle promotion, incomplete-idempotency, valid
  idempotency, and fail-closed tests pass.
- Ohi/Funabashi experimental and race-day regression tests pass; compileall
  passes.
- Recovered 7R trajectory is valid with the exact persisted T10/T05 captures
  and support state advances from 0/3 to 1/3.
