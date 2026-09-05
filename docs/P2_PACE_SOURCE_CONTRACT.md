# P2 Pace Source Contract

`P2_PACE_NAR` uses only South-Kanto NAR historical records. Runner `last_3f`
is a Main candidate after its safe result-status audit. Race laps are a
race-level source; the 200m trailing-segment geometry is accepted only where
the distance/lap-count invariant holds and is QA-checked against `final_3f`.
Race first-3F is emitted only at an exact 600m segment boundary; no partial
segment interpolation is permitted.

NAR does not provide a confirmed runner-level first-3F. Keibabook
runner-first-3F, its own pace labels, and non-reproducible corner information
remain `P2X_O`; they are not a P2 Main source and can only be used for QA.

Corner strings are raw group-order records. Group/tie semantics are not
inferred. Until a separate promotion gate establishes deterministic runner
position meaning and independent QA, runner-corner fields are `NOT_MODEL_READY`.
Other-flat, Market, speed, and class data are not input to pace derivation.
