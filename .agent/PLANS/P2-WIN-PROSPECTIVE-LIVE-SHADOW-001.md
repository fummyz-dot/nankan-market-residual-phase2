# P2-WIN-PROSPECTIVE-LIVE-SHADOW-001 — frozen WIN research shadow

## Scope

Connect only the frozen `P2_WIN_PROSPECTIVE_V1` research family to race-day
after Main Recommendation Evidence has committed.  Persist an immutable WIN
research record, evaluate it only after the existing post-race barrier, and
write a separate cumulative probability ledger.  Main prediction,
recommendation, Policy V2, stakes, settlement, and WIDE research remain
unchanged.

## Inputs

- `models/development/win_prospective_v1/` is immutable authority.  Its
  artifact and core documents are hash-verified at startup.
- The committed Main Recommendation Evidence bundle is the sole M0/C0 and
  predecision-reference authority.  M0 comes from `market[]`; C0 comes from
  `dev_live_v1.candidate[]` in that same immutable bundle.
- Existing race registry/result tables supply the official winner only after
  `PRE_RACE_CLOSED`.
- WIDE research code/table/output are retained as independent sibling paths.

## State and transactions

- Main evidence committed → WIN research child may start; `ANALYSIS_READY`
  never waits for it.
- Existing WIN record → idempotent reuse; a different canonical payload for
  the same `(race_key, research_bundle_sha256)` fails closed.
- Existing Main evidence + pre-post + no WIN record → rebuild only from the
  retained Main bundle.  At/after post, persist
  `WIN_RESEARCH_PREDICTION_MISSED`; never backfill a prediction.
- One DB transaction inserts one evidence row after the immutable JSON audit
  envelope is finalized.  Result evaluation writes one immutable evaluation
  row; changed official source hash fails closed.

## Time and eligibility

- `created_at > confirmation_start`, adopted capture < post, prediction
  completion < post, exact roster/hash, positive normalized M0/C0/C1, and no
  pre-race result access are required for confirmation eligibility.
- `T15_STANDARD → PRIMARY_T15`; `PRE_RACE_FALLBACK → SECONDARY_FALLBACK`.
  Other modes are retained as `NOT_CONFIRMATION_ELIGIBLE` and never merged.
- Engineering replay/before-freeze records are retained only as non-
  confirmation evidence.

## Evaluation

After the existing `PRE_RACE_CLOSED` boundary, read one official winner and
calculate race-weighted LL, multiclass Brier, winner probability, maximum
probability, and entropy for M0/C0/C1.  Cumulative summaries keep T15 and
fallback separate.  No ticket, stake, ROI, or promotion logic exists.

## Failure isolation

- Frozen-artifact, bundle, roster, numerical, DB, or child failure is WIN
  research-only.  It emits an explicit event and cannot alter Main or WIDE.
- Missing post-time prediction records a durable missed state.  A source-hash
  correction cannot overwrite a prior evaluation.

## Tests / smoke

- Unit tests cover formula identities, exact Main-reference reuse, idempotent
  evidence, payload conflict, missed/backfill behavior, evaluation, scope
  separation, WIDE coexistence, and frozen hash mismatch.
- Fresh-process temp-DB smoke covers T15, fallback, restart-before-post,
  restart-after-post, research failure isolation, WIDE coexistence, and
  post-race evaluation.
- Main prediction/recommendation/evidence bytes are compared before/after;
  runtime is measured on saved 11/12/14-runner pre-race bundles.

## Acceptance

- Frozen artifacts stay byte-identical; Main/Policy/WIDE/actual-bets stay
  untouched; pre-race result and production DB mutation counts remain zero.
- Required audit artifacts are written under
  `audit/data/p2_win_prospective_live_v1_20260826/`.
