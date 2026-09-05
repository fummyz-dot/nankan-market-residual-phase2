# P2-MARKET-TRAJECTORY-INTEGRITY-ERROR-20260828-001

## Job metadata

- Job ID: P2-MARKET-TRAJECTORY-INTEGRITY-ERROR-20260828-001
- Status: COMPLETE
- Owner: Codex

## Objective

Determine the source-proven cause of the 2026-08-28 MARKET_TRAJECTORY
`IntegrityError`, repair only that defect, and determine whether trajectory
research can be deterministically rematerialized from retained pre-race source
captures without outcome/result access.

## Boundaries and invariants

- Existing trajectory collector mark, event, summary, and rebuild primitives
  only; no new table, CLI, dependency, framework, policy/model/FS04 change, or
  synthetic/re-fetched market data.
- The existing Main Recommendation, WIN/WIDE/CURRENT research Evidence, and
  settlement/evaluation are immutable read-only inputs.
- Exact replay may no-op only when its logical key and immutable payload match.
  A different payload for that key must fail closed and retain old/new evidence.
- Rebuild uses only retained pre-race collector DB/raw/event provenance; it is
  distinguished in audit as post-live rebuild and never represented as runtime
  live success.

## Tasks

1. Locate the exact 8/28 error, source SQL/schema, call path, and transaction
   scope; classify only from retained evidence.
2. Inventory retained T20/T15/T10/T05/RECOVERY captures for races 5/7/9/10/11/12,
   preserving capture ID/time/hash/mark and missing/invalid states.
3. Reuse the narrow existing idempotency/rebuild path, or make the smallest
   source-proven repair and add focused replay/conflict/resume regressions.
4. Run a fresh process against copied/temp pre-race state; if safe, perform a
   no-network rematerialization only from the retained 8/28 sources.
5. Hash frozen Evidence before/after and write the required audit manifest.

## Acceptance tests

- First insert, same-payload replay, different-payload conflict, double rebuild,
  restart, partial/missing marks, RECOVERY separation, withdrawal, timing drift,
  Main/other Evidence invariance, and zero result access.
- Any 8/28 rematerialization preserves source mark/capture/time/hash, makes no
  mark synthetic, and reports missing marks as missing.

## Required audit

`audit/data/p2_market_trajectory_integrity_error_20260828_001/` must contain
root cause, salvage matrix, run manifest (`vcs_mode:none`, `git_commit:null`),
source/config/code hashes, evidence comparison, test/smoke results, and
overengineering audit.

## Completion

- Root cause was reproduced from the retained capture and a copied production
  evidence DB as a foreign-key failure, not inferred from the shortened event
  reason.
- The existing evidence `race_registry` parent is selected by its exact shared
  natural key before event insert; replay and payload-conflict behavior retain
  their append-only/fail-closed semantics.
- 8/28 trajectory was rematerialized only from retained pre-race captures:
  five full trajectories and 12R T20-only partial.  The audit distinguishes it
  from a live runtime success, and protected Evidence hashes are unchanged.
