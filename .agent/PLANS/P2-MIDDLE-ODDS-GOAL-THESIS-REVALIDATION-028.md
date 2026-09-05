# P2-MIDDLE-ODDS-GOAL-THESIS-REVALIDATION-028

## Job metadata
- Job ID: P2-MIDDLE-ODDS-GOAL-THESIS-REVALIDATION-028
- Title: Zero-base revalidation of the WIN/WIDE middle-odds Actual goal thesis
- Status: COMPLETE
- Owner: Codex

## Objective
Read only canonical historical OOF and committed pre-post prospective evidence to establish, without policy selection, whether the frozen high-probability middle-odds target exists and has support for WIN and WIDE across all four venues.

## Allowed inputs
- AUDIT-027 canonical OOF authorities and immutable manifests.
- Committed prospective prediction/evidence and official result/payout records dated no later than 2026-09-02, after an exact pre-post and reference-mode audit.

## Allowed modifications
- `audit/reports/P2_MIDDLE_ODDS_GOAL_THESIS_REVALIDATION_028.md`
- `audit/data/p2_middle_odds_goal_thesis_revalidation_028/`
- this job plan only.

## Forbidden actions
- Production/audit source, model, policy, threshold, V1 reference, live DB, or web modification.
- Training, calibration fitting, threshold/venue selection, or outcome access dated 2026-09-03 onward.

## Tasks
1. Revalidate historical OOF input identity and calculate fixed WIN/WIDE funnels, economics, cluster CIs, venue diagnostics, and concentration only from historical labels.
2. Locate and validate prospective committed pre-post records through 2026-09-02; preserve `T15_STANDARD` / `PRE_RACE_FALLBACK` separation and fail closed on incomplete joins.
3. Emit report/tables/manifest, classify each ticket type only under the supplied A--D definitions, and record result-access and no-write audits.

## Required artifacts
- `audit/reports/P2_MIDDLE_ODDS_GOAL_THESIS_REVALIDATION_028.md`
- `audit/data/p2_middle_odds_goal_thesis_revalidation_028/run_manifest.json`

## Acceptance criteria
- Every reported funnel stage has the requested counts, probability, hit, price, market-edge, and GER diagnostics for ALL and each venue.
- Final-stage economics, date-cluster CIs, concentration, drawdown and losing streak are present or explicitly `INSUFFICIENT_SUPPORT`.
- Prospective figures are pre-post proven and reference-mode separated; 2026-09-03 outcome access is zero.

## Leakage and temporal checks
- Historical: only dated OOF rows through 2026-07-31, with walk-forward fold proof.
- Prospective: only records committed before scheduled post, dated no later than 2026-09-02; primary confirmation uses `T15_STANDARD` exclusively.

## Process supervision
- One foreground, synchronous bounded read-only analysis. No workers.

## Run manifest requirements
- `vcs_mode: none`; `git_commit: null`; workspace/timestamps, input/output SHA-256 manifests, platform/library versions, seed `20260903`, commands, access audit, and DB-write audit.
