# P2-M12B-R1 Official Pre-Race Horse Identity Source Recovery

## STATUS

`READY_TO_RESUME_P2_M12B`.

## Source

Saved `PREDECISION_VALID` official T15 current-card raw is the primary
provenance. It contains horse name, short birth-date text, and an official
`/uma_info/<id>.do` link for every runner. The short date is not expanded by
the parser. I2 uses the link already present in the saved T15 card to obtain
the official detail page's full birth date and confirms exact card/detail horse
name plus `YY.M.D`/full-date consistency.

## 2026-08-20 audit

Kawasaki 6R–11R: 6 races, 70 runners, 69 `EXACT_MATCH`, 1
`GENUINE_COLD_START`, 0 unresolved, and 0 collision. These six saved official
pre-race cards, each with its linked horse details, also supply more than the
required five-card parser/detail fixture coverage.

## Storage and safety

The existing `current_runner_info` table was version-extended with
`horse_name_exact`, `birth_date`, `birth_date_raw`, `official_horse_id`, and
`official_horse_url`. Existing 500 rows were retained; `quick_check=ok` and
`foreign_key_check` is clean. No result DB or Keibabook input was opened for
identity recovery. No model training, prediction, performance, or ROI work
occurred.
