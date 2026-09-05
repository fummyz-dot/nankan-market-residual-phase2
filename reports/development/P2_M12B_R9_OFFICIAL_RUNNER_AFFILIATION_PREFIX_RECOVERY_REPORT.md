# P2-M12B-R9 — Official Runner Affiliation Prefix Semantic Recovery

## Status

`OFFICIAL_RUNNER_AFFILIATION_PREFIX_SEMANTICS_RECOVERED`

The fixed 2026-08-01 through 2026-08-20 R4 manifest contains 204 official
cards. The audit found exactly three leading display tokens: `[J]` (7 rows / 1
race), `[兵]` (2 rows / 2 races), and `[高]` (1 row / 1 race). Each token is
consistent with its official trainer-affiliation field (`JRA`, `兵庫`, and
`高知` respectively); every card identity name exactly matches the official
detail comparison name, with zero wrong identities and zero collisions.

Raw card display names, prefix tokens, and comparison names are stored
separately. The pre-existing detail-page terminal `（抹消）` rule remains an
independent later layer. Canonical `P2_HORSE_IDENTITY_V1` is unchanged.

## Continuation

The recovered rule promoted the previously blocked 2026-08-05 Funabashi R10
atomically. R4 then progressed through 2026-08-16 and stopped independently
at 2026-08-17 Ohi R8 on an unestablished official `result_status` semantic.
This report does not authorize interpreting that new status.
