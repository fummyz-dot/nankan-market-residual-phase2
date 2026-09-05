# P2-M01 — Full Flat-NAR Historical Context DB Build

## Job metadata
- Job ID: P2-M01
- Status: COMPLETED
- Execution: foreground sequential, monthly commits and annual atomic checkpoints

## Objective
Build a Phase 2-owned SQLite database from the 2020-01–2026-07 retained raw NAR race corpus. The formal DB contains South Kanto plus the audited other-flat NAR venues, with raw member provenance for every race and runner.

## Inputs
- Read-only `reference/v1/data/raw_nar/zips/race/`
- Read-only P2-M00 identity/provenance artifacts
- Read-only `reference/v1/db/nankan_history.sqlite` only for the cut-off South Kanto regression audit

## Invariants
- Canonical identity is exactly `馬名 + 生年月日`; no normalization, fuzzy matching, or name-only join.
- Include only `NANKAN_TARGET` and `OTHER_FLAT_NAR`; exclude Ban'ei, unknown venues, and dates after 2026-07-31.
- Target/loss/evaluation remain South Kanto only. `P2_XVENUE` remains model-use unapproved.
- No feature generation, modeling, market data, payout data, or performance evaluation.
- Every formal race/runner is traceable to an immutable archive and CSV member.
- The formal DB is atomically promoted only after integrity, uniqueness, provenance, cutoff, and completeness validations pass.

## Outputs
- `db/p2_history_context.sqlite`
- `audit/data/p2_m01/` audits, markers, yearly checkpoints, and run manifest
- `data/manifests/P2_HISTORY_CONTEXT_DB_MANIFEST.json`
- contracts, state/decision updates, report, and tests

## Failure/rebuild policy
- Existing formal DB or temporary DB is never silently overwritten.
- A failed build leaves only the explicitly named temporary DB and a FAILED marker; it is not a formal artifact.
- Rebuild requires an explicit operator cleanup/rebuild action outside this job.

## Acceptance
Promote only with zero identity/race/runner collisions, complete provenance, clean SQLite checks, expected flat counts, Ban'ei exclusion, cutoff isolation, and reconciled target-history counts.

## Completion record
- Formal DB was atomically promoted after all validations: 88,617 races and 908,784 runners.
- South Kanto regression key sets and the requested payload fields had zero differences against the cutoff-filtered V1 reference subset.
- All 79 archives / 158 read members matched M00 provenance; seven annual checkpoints and no background workers were used.
