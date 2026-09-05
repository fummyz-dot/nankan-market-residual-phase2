# P2 Current Candidate Feature Contract

`P2_CURRENT_CANDIDATE_REGISTRY_V1` freezes CUR01–CUR06 before outcome or feature-performance work. H2-C05 is only the future identity `FS04 + activated P2_CURRENT_V1`; it is `REGISTERED_NOT_EVALUATED` and no final feature list is yet frozen.

- CUR01 / CUR02: official displayed body weight and signed change only from a `PREDECISION_VALID` T15 capture. For decision time `D`, this means `D - 60 seconds <= captured_at <= D`; a later raw capture is preserved as `LATE_AFTER_DECISION` but cannot rescue T15 availability.
- CUR03: official declared current jockey compared only to a deterministically matched Nankan jockey from a strictly earlier calendar date. Same-day and other-flat prior context are prohibited; no fuzzy identity matching is used.
- CUR04 / CUR05: official predecision weather and track-condition values only. They remain unactivated when the source/parser is unresolved.
- CUR06: count of the approved snapshot-time active roster, never final starters.

Activation is source-quality-only: deterministic parsing, `PREDECISION_VALID` availability evidence, no postdecision fallback, zero joins/duplicates, overall >=97% and each venue >=95% coverage. Field coverage denominators include only valid predecision T15 captures; `STALE_FOR_T15`, `LATE_AFTER_DECISION`, and `MISSED` cannot increase coverage. Legitimate missing prior Nankan history for CUR03 is a cold start, not source-capture failure. Outcomes, Market LL, residuals, ROI, and feature importance are prohibited from activation decisions.
