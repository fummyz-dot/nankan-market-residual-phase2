# P2-WIDE-FUNABASHI-EXPERIMENTAL-V0-001

## Scope

Add an outcome-blind, manual-only Experimental V0 above the accepted
Funabashi Shadow V0.  It consumes immutable Shadow evidence; it does not
change Shadow, Main, frozen WIDE-P0/J1, or `actual_bets`.

## State

1. Persist one immutable arm observation per processed Shadow race.
2. The first three valid Shadow terminal decisions are the fixed arm window.
   They require at least one `SHADOW_ONLY` and no observed operational or
   integrity failures.  The third race remains Shadow-only.
3. Persist immutable `armed.json`; only a later distinct pre-race decision
   can become a manual recommendation.
4. Persist recommendation/no-buy intent separately, enforcing three tickets
   and 300 yen per date.  A conflict/corruption/scale violation persists a
   suspension; ordinary market/J1/reference misses are race-local no-buys.

## Boundaries

- Arm and recommendation paths read only Shadow JSON and their own artifacts.
  They never open result, payout, settlement, or actual-bet sources.
- Manual recommendation is output only.  No automatic purchase or actual-bet
  record exists.
- Post-race recommended-ticket evaluation, when called with official payout,
  writes a separate JSON and never alters intent evidence.

## Validation

Synthetic tests cover arm timing/window failures, daily cap, restart,
suspension, result blindness, P0 boundaries, and Main/Shadow isolation;
existing race-day and prospective WIDE tests remain regression coverage.
