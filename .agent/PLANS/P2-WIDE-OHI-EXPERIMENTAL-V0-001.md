# P2-WIDE-OHI-EXPERIMENTAL-V0-001

## Scope

Add a manual-only Ohi Experimental V0 which consumes immutable Ohi
price-support state and its already frozen T15 WIDE-P0 selection. It is
independent from Funabashi Experimental V0 and from Main.

## Inputs and state transitions

1. Read only `wide_ohi_price_shadow_v0/state/price_support.json` and verify
   its parent schema, policy, outcome-free contract, terminal state, and exact
   bytes.
2. `PENDING` and `NOT_ELIGIBLE` remain disabled. For `ELIGIBLE`, the final key
   in `first_three_valid_race_keys` is the existing effective-after race.
3. Read the corresponding immutable parent T15 selection instead of selecting
   WIDE-P0 again. The effective race itself and any T15 evidence created no
   later than the terminal state remain no-buy.
4. A later valid Ohi T15 selection creates one immutable Ohi intent, subject
   to the fixed 100-yen, two-ticket/200-yen daily cap. Parent-state SHA is
   bound to every intent.
5. Any parent-state/intent/scale corruption suspends Ohi only. Ordinary
   reference, market, J1, and no-ticket cases are race-local no-buys.

## Boundaries

- No result, payout, hit, return, ROI, settlement, or `actual_bets` source is
  opened in activation or recommendation paths.
- The existing explicit purchase-confirm CLI gains only the exact Ohi policy
  and schema/root allowlist entry; unknown policies remain rejected.
- Race-day renders this layer after Ohi price shadow, never changes Main, and
  does not alter Funabashi paths.

## Validation

Synthetic tests cover all price-support states, effective-next-race timing,
P0 boundaries, cap, SHA binding, conflict suspension, purchase-confirm
allowlist, outcome isolation, and race-day/Main/Funabashi isolation.
