# P2-CURRENT-PREVIOUS-JOCKEY-IDENTITY-CONTRACT-V1-026

## Objective

Make prospective P2_CURRENT jockey context use the immutable Main runner
identity audit, then resolve CUR03 only against the last strictly-prior
Nankan actual start and compare official jockey IDs.

## Inputs

- Main live-shadow materialized `identity_audit`, carried into the immutable
  analysis bundle.
- Retained authoritative CURRENT card for the current jockey ID.
- `p2_history_context.sqlite` and
  `p2_live_history_normalized_delta.sqlite` for prior Nankan starts.

## Invariants

- CUR03 remains `REGISTERED_NOT_ACTIVATED`, context/research-only, and
  outside FS04, DEV-LIVE-V1, recommendation, and race-day blocking logic.
- Prior races are Nankan-only and strictly `race_date < target_date`.
- `starter_status()` remains the approved actual-start classifier.
- SAME/CHANGED require official IDs on both sides.  Raw names are provenance
  only.
- Existing V1 sidecar evidence is never overwritten; new evidence is V2.

## State and idempotency

1. Persist the Main-resolved identity audit in the immutable Main bundle.
2. Sidecar V2 reads the exact runner identity from that bundle; unavailable or
   ambiguous identity produces `UNKNOWN`.
3. It deduplicates base/delta by canonical race key before selecting the
   latest actual Nankan start, preferring the delta only for an official
   jockey ID on that selected race.
4. Existing V1 outputs remain historical provenance; no backfill occurs.

## Validation

- Focused synthetic tests cover Main identity use, current-jockey anchor
  authority, start eligibility, base/delta behavior, derivation, as-of,
  nonblocking behavior, and retained pre-race fixtures when available.
- Run relevant current/P7 regressions and `compileall` with `.venv-p2-model`.
