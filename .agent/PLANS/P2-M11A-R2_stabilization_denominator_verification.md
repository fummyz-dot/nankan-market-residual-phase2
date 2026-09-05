# P2-M11A-R2 — Stabilization Denominator Verification

## Objective

Verify that the active V2 gate counts distinct eligible races, never runners,
for both the total-80 and per-venue-10 requirements.

## Invariants

- No outcome, performance, payout, or ROI access.
- No changes to non-denominator gates.

## Acceptance

- A single race with 12 runners fails the 10-race venue gate.
- Ten distinct valid eligible races satisfy it.
- Dashboard, config, test, and report all explicitly use race count.

## Status

Completed pending test/audit execution.
