# P2-WIDE-TRIO-IMMUTABLE-JOINT-REUSE-HOTFIX-026

## Status

`COMPLETE`

## Frozen objective

Replace TRIO's duplicate WIDE J0/J1 reconstruction with exact persisted subset
floats from the committed `wide_research_evidence` row, and defer TRIO launch
until a successful WIDE child has completed the venue's current WIDE action
phase. No threading, solver, model, policy, science, or FS04 behavior changes.

## Inputs and outputs

- Inputs: immutable Main evidence, committed exact-WIDE evidence payload, frozen
  WIDE/TRIO manifests, retained pre-race materialization, and existing tests.
- Outputs: narrow changes to `trio_research_shadow.py` and `race_day.py`,
  focused unit/integration tests, the mandatory parity/performance artifacts,
  and a gitless run manifest.

## Invariants and exclusions

- Exact race/model/Main bundle/reference/capture/snapshot provenance is required.
- No WIDE evidence means unavailable; invalid WIDE evidence means invalid;
  neither path recomputes the WIDE joint.
- Existing TRIO idempotency exits before materialization/load.
- WIDE must remain byte- and hash-invariant; TJ0/TJ1 are copied persisted floats.
- OHI WIDE Price Shadow and OHI Experimental run before deferred TRIO start.
- Result/payout/settlement access and production DB writes are zero during
  parity/performance verification.

## Failure cases and acceptance

Cover missing/invalid WIDE evidence, wrong bundle/Main/reference, duplicate or
missing subsets, nonfinite/nonpositive values, forbidden recomputation,
idempotent TRIO short path, WIDE-ready scheduling, WIDE-failure suppression,
OHI ordering, and restart ordering. Verify 2026-09-02 1,870-set artifact
identity and run bounded 11R outcome-free timing.
