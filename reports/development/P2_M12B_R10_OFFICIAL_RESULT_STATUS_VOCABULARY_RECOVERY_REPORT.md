# P2-M12B-R10 Official Result-Status Vocabulary Semantic Recovery

## STATUS

`OFFICIAL_RESULT_STATUS_VOCABULARY_RECOVERED`

This recovery is a source-semantic and live-history ingestion change only. It
does not train, score, evaluate performance, read payout/ROI, or change a
frozen feature/model protocol.

## Exact current token

The preserved official result raw for 2026-08-17 大井8R, horse 10 キテツ,
displays `同着` in both the finish and margin cells, with time `1:28.1` and
last-3F `39.1`. The immediately preceding official row is numeric rank 2 with
the same finish time. The live parser preserves `finish_position_raw=同着` and
uses the narrow configured rule to represent the official shared rank as
`FINISHED`, `finish_position=2`. It does not create a terminal rank.

## Bounded vocabulary

All 204 R4 result pages were scanned: 165 retained committed result raws were
reused and 39 remaining official result pages were retained for the audit.
The observed vocabulary consists only of numeric finish, `同着`, `出走取消`,
`競走中止`, and `競走除外`. All map to existing frozen M07 outcome classes;
no observed token remains unresolved. The exact records are in
`audit/data/p2_m12b_r10/official_result_status_vocabulary.csv`.

`同着` is allowlisted only as an exact finish display with exact raw margin
`同着`, a preceding positive numeric official rank, and an identical official
finish time. Unknown nonnumeric displays remain unresolved and block. R8's
`競走中止` semantics are unchanged.

## Historical evidence and R4 resume

Frozen historical context contains 385 `margin_raw=同着` runners over 381
races, all represented as `FINISHED` with a positive numeric shared rank.
Three later-race historical fixtures (38 runner rows) reproduce FS04-178 with
zero mismatches and maximum numeric difference `4.875266856885219e-13`.

Ohi 8R committed atomically. R4 then resumed through all remaining races:
204/204 races and 2,130 runner rows are present in the append-only delta;
`quick_check` is `ok` and `foreign_key_check` is empty.

## Remaining hard gate

The collection/backfill portion of R4 is complete. `P2HistoricalAsOfView`
proves date visibility/freshness but is not yet consumed by the actual
V1/Class/Speed/Pace online state builders. Therefore the required simulated
base+delta shadow-cutoff FS04 parity has not been established. P7--P11 remain
blocked at `BLOCKED_ON_LIVE_HISTORY_SHADOW_CUTOFF_PARITY`; no live inference
or prediction freeze was attempted.
