# P2-RACE-DAY-COMPACT-POST-WAITING-HOTFIX-017

## Inputs

- Race-day compact renderer and frozen 0/10/20 CLI outcome classifier.
- Valid top-level lifecycle/termination payloads, including those that do not
  originate from a `RACE_DAY_READY` envelope.

## Output

- Compact rendering for post-race waiting and other direct terminal statuses
  without a `targets` dependency.

## Invariants

- Exit classification, orchestration states, and `--json` output remain
  unchanged.
- The established detailed `RACE_DAY_READY` rendering remains unchanged.
- Rendering never raises `KeyError` for a valid direct lifecycle payload.

## Verification

1. Ready output remains byte-for-byte stable.
2. Direct `POST_RACE_WAITING`, `DAY_COMPLETE` with history pending, and
   invariant failure render without `targets`.
3. Existing 0/10/20 main outcome matrix remains unchanged.
