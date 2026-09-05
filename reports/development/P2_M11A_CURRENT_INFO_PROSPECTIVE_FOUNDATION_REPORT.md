# P2-M11A Current Information & Prospective Foundation

## STATUS

`READY_FOR_P2_M11_STABILIZATION_ACCUMULATION`

P2_CURRENT has a raw-provenance SQLite schema, an official-only foreground day collector, and a deterministic stabilization dashboard. No outcome, training, market-residual, ROI, or feature-performance calculation ran. H2-C05 remains registered but unevaluated; H2-C06 remains unallocated.

## Existing fixture

Kawasaki 5R on 2026-08-19 has four bodyweight/current-card raw captures and 44 runner snapshot rows. Parser parity is PASS for T20/T15/T10/T05. Their capture timestamps occur about five seconds after the nominal marks, so they demonstrate mechanics only and are `NOT_PROVEN_PREDECISION`; they cannot activate a T15 feature.

## Stabilization

Calendar days: 1; eligible races: 0/0; readiness: `False`. Future collection uses a 10-second lead before each T20/T15/T10/T05 decision mark and records missed marks without backfill.

## Candidate boundary

CUR01–CUR06 are frozen as source-quality candidates only. CUR04/CUR05 remain source-unresolved. No candidate is activated by performance. P2_CURRENT remains separate from P2_MKT; multiple Market snapshots generate no trajectory feature in M11A.

## Next stage

Accumulate outcome-free timestamped collection under `python3 -m src.operations.prospective_day_collector --date YYYY-MM-DD`, then review the fixed gate after its non-outcome thresholds are met. T15 remains an engineering candidate and is not frozen.
