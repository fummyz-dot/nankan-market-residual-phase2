# P2-M12B-RESUME2 Online Inference / Shadow Pipeline Report

## STATUS

`BLOCKED_ON_ONLINE_CLASS_PARITY`

## Completed position

R1 identity and R2 official-direction contracts remain valid and were not
redone. `P1_ONLINE_V1_119` passed: 55 runners across five fake-live historical
fixtures matched all 119 M06 V1 fields exactly, including null masks and
categoricals; numeric maximum absolute difference was `0.0`.

## Blocking phase

`P2_ONLINE_CLASS_24` detected 131 exact-parity mismatches (maximum numeric
difference `8.0`). The first adapter ran several virtual historical targets in
one sequence. A virtual target correctly has no outcome/update, but that also
removed an earlier fixture race's historical class-rating update before a later
fixture date. Thus the comparison is not an exact representation of an
individual fake-live target.

No tolerance was relaxed and no feature/model/fold/gamma/backend adjustment
was made. The failed audit is retained at
`audit/data/p2_m12b/online_class_parity.csv`; P2 must be recovered before any
Speed, Pace, FS04, model, inference, prediction, bundle, or replay phase can
begin.

## Exclusions

No LightGBM training, model prediction, Market residual performance, result or
payout DB access, August-outcome use, ROI, H2-C05 evaluation, or H2-C06
allocation occurred.
