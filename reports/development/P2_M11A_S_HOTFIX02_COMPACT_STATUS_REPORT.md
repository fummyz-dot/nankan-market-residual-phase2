# P2-M11A-S-HOTFIX02 — Compact Human Collector Status

## STATUS

`READY_FOR_COMPACT_LIVE_MONITORING`

The read-only status command retains its command path and now defaults to a
compact human health view. It has `--verbose` for per-race details and `--json`
for raw structured output. Exit codes are `0=HEALTHY`, `1=WARNING`, and
`2=ERROR`.

Future `WAITING` marks are not warnings. Due T15 marks are healthy only when
`PREDECISION_VALID`; late/stale/missed/failed states remain warnings. Preserved
`P2-OPS-001` is shown as a historical race-scoped warning, distinct from a fatal
collector error. The status implementation remains read-only; collector capture
logic, database schema, timing semantics, outcomes, and performance paths were
not changed.

## Verification

Eight compact-status tests and the existing M11A-S/HOTFIX01 regression tests
pass. The default live command renders eight lines.
