# P2-M01 — Full Flat-NAR Historical Context DB Build Report

## 1. STATUS
`READY_FOR_P2_M02_CLASS_RULE_FOUNDATION`

## 2. Source scope
79 immutable raw NAR race ZIP archives (2020-01–2026-07); `racelist` and `horselist` only. The formal DB includes only the audited South Kanto 4 venues and other-flat NAR 10 venues.

## 3. Build method
Foreground sequential monthly ingestion, explicit SQLite transactions, annual atomic checkpoints, temporary DB validation, then atomic promotion.

## 4. Venue counts
Nankan runner rows: 250,093; other-flat runner rows: 658,691; formal Ban'ei rows: 0.

## 5. Identity
`P2_HORSE_IDENTITY_V1` uses a SHA-256 key over exact raw `馬名 + 生年月日`, with no fuzzy/name-only fallback. All rows retain exact name and birth date. Rename linking remains `NOT_RESOLVED`.

## 6. Table counts
{"build_metadata": 6, "horses": 43544, "identity_audit": 43544, "race_runners": 908784, "races": 88617, "source_archives": 79, "source_members": 158, "target_horses": 18965}

## 7. Provenance
All 88,617 races and 908,784 runners resolve to a source member; all 158 read source members matched P2-M00 SHA-256/row-count provenance.

## 8. Nankan regression
Race key-set delta: 0; runner key-set delta: 0. Payload-level differences, if any, are recorded as raw-source-vs-V1 warnings and do not rewrite raw values.

## 9. Target history completeness
Target horses: 18,965; with other-flat history: 9,290; added other-flat rows: 165,475; total context rows: 415,568.

## 10. Temporal safety
Maximum stored race date: 2026-07-31. Rows after cutoff and the 128 post-cutoff V1 rows used: 0. Future feature builders must use strictly earlier race dates; same-day is prohibited.

## 11. Ban'ei isolation
107,198 Ban'ei source runner rows were audited and excluded; none enter the formal DB.

## 12. SQLite integrity
`PRAGMA quick_check`: `ok`. `PRAGMA foreign_key_check`: clean.

## 13. Data quality
Field-size mismatches: 0 (profiled, not repaired). Raw class/event semantics remain untransformed. Historical outcomes are stored only for past-history use and remain prohibited for current target-race feature joins.

## 14. Resource usage
Elapsed 32.675 seconds; peak RSS 684440 KiB; seven annual checkpoints; no background/child workers.

## 15. Next stage
P2-M02 class-rule foundation may consume the provenance-linked DB under its own strict-as-of and source-boundary contract. `P2_XVENUE` model use remains unapproved.
