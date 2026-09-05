# P2-OHI-20260902-POST-LIVE-AUDIT-023

## Inputs

- Immutable 2026-09-02 Ohi pre-race bundles, recommendation/research evidence, and race-day events.
- Persisted official result/payout, result-completeness, and Actual Purchase evidence.

## Outputs

- `audit/reports/P2_OHI_20260902_POST_LIVE_AUDIT_023.md`
- `audit/data/p2_ohi_20260902_post_live_audit_023/run_manifest.json`

## Invariants

- Read-only data inspection; no source, DB, artifact, model, policy, or threshold mutation.
- Use persisted official and Actual Purchase evidence as the economic source of truth.
- Keep actual purchases, descriptive policy cohorts, research hypotheticals, and context-only observations separate.
- Do not compute new model performance statistics or propose decision changes.

## Acceptance checks

- All seven specified races are covered from persisted evidence.
- Actual accounting is reconciled to immutable purchase confirmations.
- Explicitly record the 7R research-miss and known operational incidents.
- Record inspected sources and command provenance in the run manifest.
