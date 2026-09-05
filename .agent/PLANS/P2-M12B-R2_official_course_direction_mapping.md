# P2-M12B-R2 — Official Course Direction Mapping

## Inputs

- Official Nankan course-information pages for Ohi, Funabashi, Kawasaki, and Urawa.
- Saved 2026-08-20 Kawasaki T15 pre-race captures in `db/market_snapshot.sqlite`.
- Historical canonical direction values in `db/p2_history_context.sqlite` for QA only.

## Outputs

- Versioned official direction mapping config with raw-source provenance.
- Deterministic D1/D2/D3 direction resolver.
- Historical parity and 2026-08-20 Kawasaki resolution audits.
- Contract, decision record, report, manifest, and regression tests.

## Invariants and exclusions

- Mapping source is official static course metadata; historical direction is QA only.
- Explicit official pre-race direction takes priority; conflict blocks inference.
- Ohi has no unlisted-distance fallback.
- No model training, prediction, result/payout access, or performance calculation.

## Acceptance tests

- All four venues resolve only through approved official rules.
- Ohi 1650 resolves left; approved listed distances resolve right; unknown distance blocks.
- Historical mapped combinations have zero direction mismatches.
- 2026-08-20 Kawasaki 6R–11R resolve left via static official reference.
