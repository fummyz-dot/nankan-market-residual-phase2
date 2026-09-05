# JOB007R1 Clean-room Causal Access Guard V1

Status: **FROZEN FOR CLEAN-ROOM RETRY**

The prior JOB007 attempt is invalid because post-cutoff normalized,
outcome-derived rows were opened before historical parity completed.

This invalidates the attempt, not the already frozen Stage2 scientific
specification. No JOB007 commit was created and no Stage2 performance aggregate
was computed.

## Retry start

```text
main = c118e2a7af03f96f27b75febce15d64fe1e4031a
branch = codex/job007-stage2-forward-locked-replay
branch commits ahead of main = 0
worktree = clean
```

No initial merge or main promotion is required.

## Phase A hard boundary

Until `PHASE_A_PASSED.json` exists and is bound to the exact current
implementation commit, the following files must not be opened, queried, hashed,
or inspected:

```text
/home/nabe/projects/nankan-market-residual-phase2/db/p2_live_history_delta.sqlite
/home/nabe/projects/nankan-market-residual-phase2/db/p2_live_history_normalized_delta.sqlite
/home/nabe/projects/nankan-market-residual-phase2/db/market_snapshot.sqlite
/home/nabe/projects/nankan-market-residual-phase2/db/live_development.sqlite
```

Likewise, do not inspect post-cutoff raw/live output directories.

`P2NormalizedHistoricalAsOfProvider` must not be instantiated during Phase A,
because its initialization opens the normalized delta database even for a
pre-cutoff target date.

Phase A parity must use only frozen pre-cutoff historical sources and Job003B /
Job004 artifacts.

## Runtime enforcement

Phase A must run under a Python access guard that rejects forbidden paths at
`sqlite3.connect` and ordinary Python file-open boundaries. A denied attempt is
an immediate hard block.

No ad-hoc shell/Python/sqlite inspection of forbidden data is allowed outside
the guarded audit process.

## Quarantine

Any ignored local artifacts from the failed JOB007 attempt must be moved without
reading their contents and must never be reused.

## Phase B unlock

Phase B may begin only after both historical parity gates PASS and
`PHASE_A_PASSED.json` records:

```text
current implementation commit
authority hashes
parity artifact hashes
postcutoff_live_db_open_count = 0
network_access = false
```

Only then may local post-cutoff market/history databases be opened.

Performance remains blinded under the existing Stage2 amendment.
