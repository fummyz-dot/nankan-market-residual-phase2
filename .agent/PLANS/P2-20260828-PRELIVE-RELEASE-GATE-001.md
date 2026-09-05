# P2-20260828-PRELIVE-RELEASE-GATE-001 — pre-live release gate

## Job metadata

- Status: IN_PROGRESS
- Mode: release audit only. No scientific, policy, feature, model, or production
  database change is in scope.
- Fixture authority: saved 2026-08-24 Funabashi raw captures and temporary/copied
  SQLite databases only.

## Frozen inputs

- Main: DEV-LIVE-V1, `P2_OPS_BET_POLICY_V2`,
  `P2_PRE_RACE_CAPTURE_POLICY_V1`, and `P2_RECOMMENDATION_EVIDENCE_V1`.
- Research: frozen WIN, WIDE, WIN-market-trajectory, and CURRENT prospective V1
  bundles.
- Operations: top-level `./race-day`, official result collection, and
  `race-evaluate` paths.

## Invariants and exclusions

- Production database mutations, actual bets, and pre-race result/payout/HTTP
  outcome access must remain zero.
- Saved outcomes are accessible only after the pre-race terminal barrier for the
  post-race scenarios.
- Main recommendation/evidence must commit before all research sidecars; research
  failures and expected source-quality warnings remain isolated.
- T15 and fallback scopes remain separate; RECOVERY must never be relabelled as a
  standard trajectory mark; post-race prediction/capture backfill is prohibited.
- Existing manifests and recommendation evidence are immutable on restart.

## Procedure

1. Inspect the preceding integration rehearsal, frozen bundle verification, and
   available fresh-process fixtures. Record component hashes before scenario work.
2. Run the normal T15, fallback, V2 wide-only, CURRENT contamination/withdrawal,
   research coexistence/failure-isolation, 11/12/14-runner, leakage, restart, and
   Ctrl-C/resume fixtures in fresh Python processes against temporary state.
3. After the pre-race barrier only, run result/evaluation, target-final-race, and
   idempotency/integrity checks. Verify no post-race synthetic capture/prediction.
4. Write the release-gate manifest and required concise audit artifacts under
   `audit/data/p2_20260828_prelive_release_gate/`. If an immediate LIVE-blocking
   defect is reproduced, apply only the smallest repair and rerun its positive,
   negative, adjacent-normal, and top-level fresh-process checks.

## Acceptance

- All frozen component contracts validate; a deliberate mismatch is fail-closed.
- Main T15/fallback/resume and V2 WIN-only behavior pass without waiting for
  research; all research sidecars are independently idempotent and isolated.
- The 8/24 CURRENT pedigree-contamination and withdrawal regressions pass.
- Pre-race leakage access is zero; post-race lifecycle and 11R target completion
  pass; temporary DB integrity is clean; production mutation remains zero.
