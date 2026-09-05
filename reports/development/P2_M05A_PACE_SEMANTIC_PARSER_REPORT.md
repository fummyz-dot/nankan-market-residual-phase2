# P2-M05A — Pace Semantic & Parser Report

## 1. STATUS
`READY_FOR_P2_M05B_WITHOUT_NAR_RUNNER_CORNER`

## 2. Runner last-3F
Safe FINISHED observations: 244494 / 250093. Within-race median-relative advantage and average-tie rank percentile are deterministic where at least two safe runners exist.

## 3. Lap geometry and first-3F
The raw 15-slot Haron source is preserved as variable arrays. The distance/count invariant permits geometry for 21667 races. Race first-3F is emitted only at exact 600m boundaries; no partial segment interpolation occurs.

## 4. Final-3F and pace balance
Exact lap-final3F validation matches: 21667; mismatches: 0. Pace balance is available for 16959 races.

## 5. Corners and external boundary
Corners are tokenized in raw group order only. Group semantic is not inferred and Keibabook QA is not comparable because of A01 year ambiguity. NAR runner corners remain `NOT_MODEL_READY`; runner first-3F remains P2X-O only.

## 6. Next stage
M05B may build strict-as-of history from last-3F and safe race pace observations only.
