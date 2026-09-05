# P2-M00 — Horse Identity & Historical Context Report

## 1. STATUS
`READY_FOR_P2_M01_HISTORICAL_CONTEXT_BUILD`

## 2. Raw corpus
Read-only scan of 79 monthly race ZIP archives from 2020-01 through 2026-07. `racelist` and `horselist` each had 1 retained schema variant; their raw column lists are preserved in `raw_schema_variant_inventory.csv`. No explicit raw-native horse-ID/registration-code field was observed. Only `racelist` and `horselist` members were read; no odds, payout, model, or result-dependent operation was performed.

## 3. Venue universe
Observed venues: 15. South Kanto target venues: 4; other flat NAR venues: 10; Ban'ei: 1; unknown: 0. Ban'ei is excluded from the flat-history context.

## 4. Horse identifiers
No raw-native horse registration/horse-code column was present in the retained horselist schema. Exact raw `馬名 + 生年月日` had 1,015,982/1,015,982 coverage. No fuzzy normalization was used.

## 5. Collision / uniqueness
Flat-universe static profile collisions: 0. Name-only collisions: 107; name-only is not an approved identity. Sex lifecycle variants (928) are audited separately because 牡→セン is not an identity split. Ban'ei cross-conflicts (1) are excluded rather than joined. Because no stable native identifier exists, renamed/display-name variants cannot be measured; exact matching deliberately avoids fuzzy joins and may conservatively miss such rows.

## 6. V1 horse_key
`horse_key` is opaque. Retained V1 tools do not evidence its construction or collision handling. The pre-cutoff South Kanto database comparator has 18,965 exact raw-composite matches and 0 unmatched composites; no extension of the V1 key to raw all-venue data is made.

## 7. Recommended canonical identity
For this audited raw corpus and flat-history completeness only: `NAR_RAW_NAME_BIRTH::exact_raw_馬名\x1fYYYY-MM-DD`. It is valid only under the documented 2020-01–2026-07 retained raw schema and must not be treated as a general production identifier without another audit.

## 8. Target horse universe
Pre-cutoff South Kanto target horses: 18,965; South Kanto runner-history rows: 250,093.

## 9. Cross-venue history completeness
Target horses with other-flat history: 9,290; without: 9,675. Other-flat context rows: 165,475; total South-Kanto-plus-other-flat context rows: 415,568. This is completeness evidence only; `P2_XVENUE` is not approved for model use.

## 10. Temporal safety
Raw rows after 2026-07-31 were excluded. The 128 known post-cutoff history rows are not used. Future construction must require `history.race_date < target.race_date`; same-calendar-date history remains prohibited until an event-order proof exists. `horses.last_seen_date` remains prohibited.

## 11. Provenance
Every read racelist/horselist member has archive path, archive SHA-256, member SHA-256, month, and row count in `source_provenance_audit.csv`.

## 12. DB feasibility
`db/p2_history_context.sqlite` is schema-draft only. No full context DB was built in this job; a future build must retain raw archive/member lineage.

## 13. Data quality
Raw race type labels (`普通`, `準重賞`, `特別`, `重賞`) remain unclassified for official/non-standard event semantics. They were not silently promoted into the normal-race universe.

## 14. Resource usage
Foreground sequential scan: 11.6 seconds, peak RSS 343120 KiB, seven annual atomic checkpoints. No child/background processes were used.

## 15. Next stage
Proceed to P2-M01 historical-context build only under the two new contracts, preserving the target/evaluation boundary and the non-approval of cross-venue modeling.
