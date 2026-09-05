# P2-M05A — NAR Pace / Lap / Corner Semantic & Parser Freeze

## Scope

Audit and deterministically parse South-Kanto NAR runner `last_3f`, race laps,
and race corner passage into post-race observation prototypes only. This job
does not aggregate horse history or use Market/model performance.

## Inputs

- Read-only `db/p2_history_context.sqlite`, NANKAN_TARGET through 2026-07-31
- A01 raw schema/feasibility artifacts
- Keibabook ability samples only for optional QA; never a parser input or Main
  source.

## Invariants

- Runner first-3F is not reconstructed from race-level NAR laps.
- Lap first-3F is emitted only when the 600m boundary is exact; no partial
  segment interpolation. `races.final_3f` remains primary only after a QA
  comparison to exact lap-derived final 600m.
- Corner parser preserves raw group/token order and declares grouping semantic
  unverified. No group is converted to a tie.
- Other-flat, Market, P2_SPD, and P2_CLASS are excluded. Exchange structure is
  retained but its M05B history policy stays undecided.

## Acceptance

- Deterministic lap/corner parsers, source registry, observation contracts,
  raw grammar and completeness audits, and unit tests.
- Explicit decision on NAR runner-corner Main readiness, plus separate P2X-O
  runner first-3F status.

## Completion record

- Completed foreground on 2026-08-19 with deterministic race and runner
  prototype logical hashes.
- Safe runner last-3F: 244,494 rows. Lap-derived final-3F matches raw
  `final_3f` in all 21,667 comparable races; exact race first-3F is available
  in 16,959 races.
- Corner strings tokenize for 21,668 races, but group semantics and independent
  QA remain unresolved. Status: `READY_FOR_P2_M05B_WITHOUT_NAR_RUNNER_CORNER`.
