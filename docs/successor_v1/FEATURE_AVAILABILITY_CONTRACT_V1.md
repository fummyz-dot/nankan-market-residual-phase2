# FEATURE_AVAILABILITY_CONTRACT_V1

Status: `FROZEN_FOR_IMPLEMENTATION`  
Historical development cutoff: `2026-07-31`  
Same-day historical results: `PROHIBITED`

## Authority and usage semantics

The source authority is [Feature Source Adjudication](../../data/manifests/feature_source_adjudication_v1.csv), SHA-256 `4fbc40ad5eac0ad069ed1fe19088c1ba02e0fa607162b77ea5267d2f764adb18`.  Its row-level usage decisions are authoritative; G0 statuses remain preserved machine triage and are not an automatic admission rule.

Current structural and listed-entry values may be used only where the adjudication permits their specific use.  Raw horse, jockey, trainer, venue identities are grouping keys, never raw GBDT input.  Result-derived history requires exactly `source_race_date < target_race_date`; same-calendar-date results are forbidden.

## Prohibitions

Current target outcomes, market/odds/popularity/payout dependencies, current body weight or change, current weather/going, first/last-seen metadata, external data, and `MARKET_TIME_UNKNOWN` are prohibited as described in the companion JSON.  A past-race outcome or condition remains a permissible *source* only when its adjudicated lagged use is allowed and the strict date guard passes.

## Namespaces and acceptance

Only the B0 and P1 namespaces enumerated in the JSON contract are candidates.  A materialized Primary feature additionally requires permitted source usage, strict-as-of implementation, same-day and future-row exclusion tests, current-outcome scan, and frozen definition.  This Job creates no feature values or formulae.
