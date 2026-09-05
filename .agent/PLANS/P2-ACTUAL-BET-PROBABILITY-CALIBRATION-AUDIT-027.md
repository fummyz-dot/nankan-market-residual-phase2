# P2-ACTUAL-BET-PROBABILITY-CALIBRATION-AUDIT-027

## Job metadata
- Job ID: P2-ACTUAL-BET-PROBABILITY-CALIBRATION-AUDIT-027
- Title: Actual betting policy redesign pre-audit of frozen OOF absolute probabilities
- Status: COMPLETE
- Owner: Codex

## Objective
Audit only canonical, strict OOF WIN and WIDE predictions through 2026-07-31 to establish whether their absolute probabilities are sufficiently calibrated and supported for later policy design.

## Allowed inputs
- `audit/data/p2_win_residual_shrinkage_20260826/`
- `audit/data/p2_wide_j1_d1_joint_20260825/`
- frozen manifests and fold contracts those artifacts explicitly name
- read-only historical odds sources only when already required to form the canonical OOF records.

## Allowed modifications
- `audit/reports/P2_ACTUAL_BET_PROBABILITY_CALIBRATION_AUDIT_027.md`
- `audit/data/p2_actual_bet_probability_calibration_audit_027/`
- `src/audit/` and `tests/unit/` only if a narrow audit script and regression tests are necessary.

## Forbidden actions
- Production source, model, policy, threshold, live database, V1-reference, outcome-source, or web modifications.
- Training, refitting, calibration fitting, threshold optimization, or any access to August/September prospective outcomes.

## Tasks
1. Establish canonical WIN/WIDE OOF sources with dated fold and immutable-manifest proof; fail closed if source semantics or outcomes are unavailable.
2. Run deterministic date-cluster bootstrap (seed `20260903`, at least 10,000 draws) and descriptive requested cross-tabs from those sources only.
3. Write a provenance-manifested report and machine-readable tables; verify no production or live DB write and no prohibited outcome access.

## Required artifacts
- `audit/reports/P2_ACTUAL_BET_PROBABILITY_CALIBRATION_AUDIT_027.md`
- `audit/data/p2_actual_bet_probability_calibration_audit_027/run_manifest.json`

## Acceptance criteria
- WIN/WIDE source paths, columns, cutoff, fold proof, and absolute-probability semantics are explicit.
- Tables cover all pre-specified probability/odds/edge-gate views and the requested clustered CIs.
- Report answers Q1--Q6 without threshold selection.
- No result access later than 2026-07-31; no production source or DB change.

## Leakage and temporal checks
- Accept rows only where `race_date <= 2026-07-31`, date/fold validation records agree with frozen walk-forward authority, and labels are embedded in the approved frozen OOF artifact.
- Audit and report zero rows/outcomes dated 2026-08-01 onward, specifically 2026-09-02 and 2026-09-03.

## Process supervision
- One foreground, synchronous bounded process; no child workers.

## Run manifest requirements
- `vcs_mode: none`; `git_commit: null`; workspace, timestamps, SHA-256 input/code/config/output manifests, platform/library versions, seed, commands, and write/access audits.
