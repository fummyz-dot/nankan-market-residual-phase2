# P2-OHI-WIDE-MANUAL-ACTIONABILITY-GUARD-HOTFIX-019

## Inputs

- Existing immutable Ohi price-support and T15 selection evidence.
- Existing Ohi Experimental V0 final candidate/cap gates.
- Existing frozen WIDE research prediction payload and commit path.

## Outputs

- A final manual-action safety guard: recommend only at `seconds_to_post >= 300`.
- Observational Ohi intent fields that preserve a late valid candidate separately
  from a non-candidate.
- Observational WIDE child timing returned to the supervised child log, outside
  scientific payload and payload hashes.

## Invariants

- Existing no-buy gates retain precedence over the actionability guard.
- No frozen prediction, recommendation, model, or scientific payload hash
  includes timing data.
- The WIDE numerical order and subprocess lifecycle remain unchanged.
- No result, payout, settlement, or purchase execution access.

## Verification

1. Boundary and gate-precedence tests for Ohi Experimental V0.
2. Timing metadata, nonnegative durations, and identity-invariance tests for
   WIDE research.
3. Existing WIDE/Ohi and race-day regressions plus `compileall`.
