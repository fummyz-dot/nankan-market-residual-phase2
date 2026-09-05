# P2-LIVE-20260824-W1-SMOKE — Pre-Live Runtime Verification

## Job metadata

- Job ID: P2-LIVE-20260824-W1-SMOKE
- Title: Pre-Live Runtime Verification After Withdrawal Parser Change
- Status: COMPLETE
- Owner: Codex

## Objective

Verify, in fresh Python processes, that the exact official pre-race token
`取消` is excluded from the target active roster while an ordinary card remains
unchanged, without accessing a result source or mutating any production DB.

## Allowed inputs

- Saved official pre-race cards for 2026-08-24 Funabashi 5R and 6R.
- Approved static horse-detail cache and normalized live-history cache,
  read-only only.
- Existing 2026-08-20 Kawasaki 8R engineering-replay inputs.

## Allowed modifications

- This plan and `src/audit/` smoke harness.
- `audit/data/p2_live_20260824_w1_smoke/` audit artifacts.
- Existing engineering-replay output only through the explicitly requested
  `race-shadow --engineering-replay` route.

## Forbidden actions

- Production source or DB changes.
- Today's prediction or prediction freeze.
- Result/reconciliation data access.
- Model retraining/search, performance evaluation, or ROI evaluation.

## Tasks

1. In a fresh audit process parse the retained 5R and 6R cards and resolve
   active identities only from the retained approved official detail cache.
2. Exercise active-roster construction and the exact shared T15 roster
   reconciliation helper with positive and withdrawn-conflict in-memory data.
3. In a separate fresh process run the existing 2026-08-20 engineering replay
   through the top-level `race-shadow` entrypoint.
4. Verify production DB fingerprints are unchanged and write the run manifest.

## Acceptance

- 5R: 12 active, no withdrawal or unresolved parser/identity row.
- 6R: #3 `取消` maps to `PRE_RACE_WITHDRAWN`; 11 active; #3 is absent from
  target FS04/Candidate/Market projections, but its raw audit row remains.
- Exact active 11-row T15-like fixture passes; a fixture retaining #3 blocks
  with `T15_WITHDRAWN_ROSTER_CONFLICT`.
- Existing 8/20 top-level engineering replay remains PASS with result access 0.
- Production DB fingerprints are unchanged.

## Completion

- Fresh-process retained-card smoke passed: 5R has 12 active runners and no
  withdrawal, identity, or parser unresolved row.
- 6R #3 `レンダリング` retained its raw `取消` audit record, normalized to
  `PRE_RACE_WITHDRAWN`, and was absent from all active target projections;
  11 active rows remained.
- The shared roster reconciler passed the exact 11-row positive fixture and
  produced the exact `T15_WITHDRAWN_ROSTER_CONFLICT` negative block.
- The top-level `./race-shadow --date 2026-08-20 --venue 川崎 --race 8
  --engineering-replay` fresh-process command passed with FS04-178 and result
  access 0.  Both production DB SHA-256 fingerprints were unchanged.

## Run manifest

- `vcs_mode: none`; `git_commit: null`; source/input/config SHA-256 manifests;
  Python/platform metadata; null random seed; commands and artifact hashes.
