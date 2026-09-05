# P2-SPECIALIZED-COLLECT-LIVE-ENTRYPOINT-HOTFIX-034

## Root cause

033 exposed only `validate`, `ingest`, `replay`, and `status`.  No subcommand
resolved a live day, scheduled T15 work, or ran a collection loop.

## Change

- Preserve maintenance subcommands.
- Make no arguments enter collection-only live mode.
- Reuse official day discovery for a no-write dry run; fixture mode supplies a
  deterministic foreground live-loop test without network or outcomes.
- Verify the frozen contract before plan/run and prove no buy path.

## Invariants

- Normal live command has no required subcommand.
- It is foreground, finite, and auto-exits after the final T15 obligation.
- P4 schedule is recorded as low priority and cannot delay T15.
- No model, policy, recommendation, settlement, or outcome evaluation.

## Acceptance

- A real subprocess invokes `./specialized-collect` with no arguments under a
  synthetic day fixture, schedules multiple T15 events, persists/finalizes a
  manifest, and exits zero.
- `--help` and all maintenance subcommands work.
- Today’s official dry-run prints venue/race count and first/last exact T15.
