# P2-WIN-PROSPECTIVE-FREEZE-V1-001 — WIN prospective research freeze

## Scope

Create only a versioned, hash-sealed research contract under
`models/development/win_prospective_v1/`.  The contract compares the existing
live calibrated WIN market (M0), the existing `DEV-LIVE-V1` prediction (C0),
and the registered one-parameter shrinkage transform (C1).  It does not add a
database table, modify race-day/race-shadow, fit a model, or alter policy.

## Inputs

- `audit/data/p2_win_residual_shrinkage_20260826/lambda_devfull.json` is the
  sole lambda authority.  Its exact JSON numeric value is retained.
- `data/manifests/P2_DEV_LIVE_V1_MODEL_MANIFEST.json` and
  `models/development/dev_live_v1/model.txt` are the C0 version/hash
  authority.
- Existing pre-race-only live bundles for 2026-08-24 Funabashi races 05, 06,
  and 10 are smoke inputs (12, 11, and 14 runners respectively).
- The existing `shrink_probabilities` implementation from the shrinkage audit
  is reused for the C1 transform.

## Contract and invariants

- M0 is exactly `market_calibrated_probability` emitted by the existing
  race-shadow predecision bundle; C0 is exactly its
  `candidate_probability` row.  C1 is only
  `softmax(log(M0) + lambda * log(C0/M0))`.
- Every model probability must be positive, finite, roster-exact, and sum to
  one.  Lambda endpoints 0/1 reproduce M0/C0 exactly; runner ordering cannot
  change a horse-number result.
- The research family is `P2_WIN_PROSPECTIVE_V1`; C1 remains a prospective
  challenger with `NO_RESIDUAL_SIGNAL`.  HS01 is explicitly excluded.
- The prospective primary scope is `T15_STANDARD`; `PRE_RACE_FALLBACK` is a
  separately reported secondary scope.  Predictions must be created strictly
  after the frozen `confirmation_start` timestamp.
- The canonical bundle-content hash covers the four scientific contract files
  and deliberately excludes the closing manifest.  This avoids a self-hash
  cycle.  The closing `artifact_manifest.json` records the base files,
  content hash, final confirmation start, and its own post-write SHA is
  recorded in the audit closure.

## Exclusions

- No LightGBM/gamma/lambda fitting, feature materialization, Horse State,
  CURRENT, Market trajectory, ROI, or policy work.
- No August outcome, result database, payout, production database, or actual
  bets access.
- No DEV-LIVE-V1, Policy V2, WIDE model, recommendation, or LIVE code change.

## Failure handling and idempotency

- Missing authority, hash/version mismatch, invalid probabilities, non-T15
  smoke provenance, or a bundle collision fails closed.
- Writes are atomic.  A pre-existing output bundle is accepted only if its
  frozen content matches; different content is never overwritten.

## Tests

- Exact lambda endpoint identities, normalization/positivity, shuffle
  invariance, and rejection of invalid rosters/probabilities.
- Fresh-process prediction-only smoke using the saved pre-race 11/12/14-runner
  bundles, with no outcome/result database access.
- Contract/hash closure and confirmation timing semantics.

## Acceptance

- Required model artifacts and task audit artifacts exist, are hash-sealed,
  and identify C0/C1 roles and the development cutoff.
- The fresh-process smoke and unit tests pass; hard-audit access counts are
  zero and the Main model/Policy remain untouched.
