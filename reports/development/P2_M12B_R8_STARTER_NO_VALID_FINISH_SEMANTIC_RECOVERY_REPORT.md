# P2-M12B-R8 — Official Starter-No-Valid-Finish Semantic Recovery

## Status

`STARTER_NO_VALID_FINISH_SEMANTICS_RECOVERED`

Raw official `競走中止` is already registered as
`STARTER_NO_VALID_FINISH` by the frozen M07 outcome registry. No outcome,
feature, Class, Speed, Pace, model, or search protocol was changed.

## Historical precedent and state behavior

The immutable historical context has 921 South-Kanto runner rows across 870
races with `margin_raw = 競走中止`. Every one retains NULL numeric finish,
finish-time, and last-3F values. The implementation audit establishes the
existing frozen effects: V1 and Class retain their existing participation and
prior-race state behavior; Class creates no pairwise/rating result update;
Speed and Pace create no runner performance observation. Race-level metadata
continues to follow its own frozen eligibility predicate.

## Exact replay and live promotion

Five later normal-start fixture races (65 runner rows) reproduce M06 FS04
exactly: 178 fields, zero mismatches, and maximum numeric difference
`5.000444502911705e-13`. Urawa 2026-08-07 R6 then committed atomically with
the stopped starter retained as `RAW_FINISH_STATUS_MISSING`, NULL finish/time/
last-3F, and raw margin `競走中止`; SQLite quick and foreign-key checks pass.

## Continuation status

R4 backfill resumed after this recovery. It later stopped independently at
2026-08-05 Funabashi R10 on an unapproved current-card `[J]` annotation versus
the official horse-detail canonical name. This report preserves R8 success; it
does not authorize normalization of that distinct source-semantic conflict.
