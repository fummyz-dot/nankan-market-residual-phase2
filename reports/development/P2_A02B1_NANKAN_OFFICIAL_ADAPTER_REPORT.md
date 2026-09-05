# P2-A02B-1 Nankan Official Adapter Report

## 1. STATUS
`READY_FOR_P2_A02B2_LIVE_FRESHNESS_TEST`

## 2. Historical fixture
2026-07-31 川崎10R, 迅速（じんそく）賞 Ｃ１ 選定馬, 12頭. All persisted market values are `HISTORICAL_FIXTURE_ONLY`.

## 3. Redirect behavior
The entry request recorded a 302 redirect from `/syousai/` to `/uma_shosai/`; the final URL was recorded rather than hardcoded.

## 4. Race identity
Page body and URL identity agreed on date, venue, and race number. Page text provided race name, 19:40 post, 900m dirt, and field size 12.

## 5. Bodyweight parse
All 12 permitted runner body-weight/change records passed the A02A positive allow-list. No market/result/prediction field is in curated output.

## 6. WIN parse
12 WIN values parsed as fixture values only.

## 7. WIDE parse
66 canonical unordered pairs parsed with numeric lower and upper odds.

## 8. TRIO parse
220 canonical unordered combinations parsed.

## 9. Odds URL discovery mechanism
The race page exposed an odds entry; the initial odds page DOM exposed the WIN/WIDE/TRIO anchors. Observed suffixes are evidence only, not a URL-generation contract.

## 10. HTTP/cache metadata
Direct WSL requests recorded request/capture times, final URL, redirect chain, status, and available cache headers. `Cache-Control: no-cache` and `Pragma: no-cache` were requested; cache bypass is not claimed.

## 11. Source displayed time
No displayed time could be safely associated to a date in this retained final-odds fixture, so `source_displayed_at` is NULL.

## 12. Historical/live isolation
No row is `PRIMARY_CANDIDATE`; all 298 snapshot rows are `HISTORICAL_FIXTURE_ONLY` and cannot be prospective prediction input.

## 13. Tests
Offline unit, integration, and leakage tests consume retained raw bytes only.

## 14. Remaining live-only unknowns
Freshness, cache behavior, source displayed-time semantics, schedule changes/scratches, and decision-time availability remain for A02B-2.

## 15. A02B-2 readiness
The live freshness test may proceed without freezing T-15 or promoting this historical fixture.
