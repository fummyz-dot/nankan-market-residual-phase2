# P2-OHI-EXPERIMENTAL-IDEMPOTENCY-HOTFIX-006

## Inputs

- Confirmed OHI Experimental V0 same-race stake self-count diagnosis.
- Active suspension: `outputs/live_development/wide_ohi_experimental_v0/state/suspended.json`.
- Required append-only history and SHA-bound resolution artifacts.

## Changes

1. Exclude the exact current P2 `race_key` from OHI daily recommended-stake aggregation.
2. Add fail-closed validation of SHA-bound suspension resolutions and preservation checks before a resolved active suspension can be replaced.
3. Add focused OHI Experimental V0 regressions and create the authorized history/resolution evidence only after validation.

## Invariants and exclusions

- Stable intent comparison remains the decision point; only `created_at` remains volatile.
- No policy, selection, odds, J1, price-support, stake-cap, purchase-confirm, or outcome semantics change.
- No result, payout, settlement, or production-DB access/write.
- `suspended.json` remains byte-for-byte unchanged by the authorized resolution.

## Acceptance

- Same-race retry is idempotent; distinct-race daily stakes/cap remain enforced.
- An exact valid resolution unblocks only its SHA-bound historical suspension; malformed resolutions fail closed.
- A later genuine suspension atomically supersedes only a resolved historical one whose exact bytes are preserved.
- Required targeted/unit parity and compile validation pass.
