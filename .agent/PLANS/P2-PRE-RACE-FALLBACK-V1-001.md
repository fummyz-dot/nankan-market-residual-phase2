# P2-PRE-RACE-FALLBACK-V1-001 — T15 Standard + Pre-race Fallback Recovery

## Objective

Keep a valid T15 capture as the only scientific-standard reference while
allowing an operational, pre-race fallback when T15 was missed and at least
120 seconds remain to scheduled post.

## Inputs

- Existing `market_snapshot.sqlite` CURRENT/WIN/WIDE capture primitives and
  `ProspectiveDayCollector` source/parser/store path.
- Existing P7 materializer, DEV-LIVE-V1 scorer, P2-WIDE-OPS-V0, and fixed
  decision policy.
- `configs/pre_race_capture_policy_v1.json` with the user-supplied fixed
  bytes.

## Invariants / exclusions

- T15 remains preferred and `scientific_sample=true`; fallback is always
  pre-race but `scientific_sample=false`.
- Fallback may only select an exact valid capture set from T20/T10/T05 or
  RECOVERY; no result/outcome DB, model, feature, policy, ledger, or result
  path access/change.
- At 120 seconds to post capture is allowed; below it produces the expected
  `SHADOW_SKIPPED_TOO_LATE` state without network access.
- WIN/WIDE must remain within one retained capture set. WIDE incompleteness is
  isolated exactly as P2-WIDE-OPS-V0 already specifies.
- Concurrent collector/race-shadow recovery requests use one per-race lock,
  recheck storage after lock acquisition, and do not duplicate a valid capture.

## State transitions

1. Valid T15 → `T15_STANDARD`.
2. No valid T15 + newest valid scheduled/RECOVERY pre-race candidate →
   `PRE_RACE_FALLBACK`.
3. No candidate + >=120 seconds → lock → recheck → bounded RECOVERY attempts
   → fallback or explicit failed invariant/recoverable exhaustion.
4. No candidate + <120 seconds → expected `SHADOW_SKIPPED_TOO_LATE`.

## Tasks

1. Add fixed policy/config and a shared capture/reference resolver with UTC
   boundary checks, DB recheck, retry classification, and per-race lock.
2. Route scheduled collector resume and `race-shadow` through that resolver;
   preserve T20/T15/T10/T05 behavior and add RECOVERY provenance/status.
3. Make P7/P8 consume the selected reference without changing model/feature
   logic; add additive provenance and compact expected-skip UX.
4. Extend collection status and add targeted temporary-DB/fresh-process tests
   plus audit/run manifests.

## Acceptance

The fixed standard/fallback ordering, boundary/retry/lock semantics, WIDE
isolation, provenance, no-result boundary, and required fresh-process cases
in the user task pass without model retraining or outcome/performance/ROI use.
