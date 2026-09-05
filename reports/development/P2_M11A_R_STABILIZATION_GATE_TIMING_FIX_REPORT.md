# P2-M11A-R Stabilization Gate Amendment & Timing Fix

## STATUS

`READY_FOR_P2_M11_STABILIZATION_ACCUMULATION`

The active gate is `P2_STABILIZATION_GATE_V2`: 14 calendar days, 80 distinct Primary-eligible races with a `PREDECISION_VALID` T15 capture, all four venues, and ten distinct valid eligible races per venue. The per-venue denominator is race count, never runner count. This operational reduction occurred before outcome/performance use. V1 is retained as superseded.

## T15 timing

Decision time is scheduled post minus 15 minutes. Only `decision_time - 60 seconds <= captured_at <= decision_time` is `PREDECISION_VALID`; older captures are `STALE_FOR_T15`, later captures are `LATE_AFTER_DECISION`. Late raw is retained but cannot prove P2_CURRENT availability or contribute to coverage. Future T15 requests begin 30 seconds early and have at most one retry before decision time.

## Existing fixture

Kawasaki 5R 2026-08-19 is preserved unchanged. Its T15 capture is `LATE_AFTER_DECISION`, not valid availability proof. Parser parity remains PASS and raw provenance is complete.

## Current dashboard

Valid predecision T15: 0/0; late: 1; stale: 0; readiness: `False`. No outcome, model, performance, payout, or ROI data was used.
