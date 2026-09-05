# P2 Race-Day V1 Operations

通常運用は次の一つだけです。

```bash
./race-day
```

PC再起動後、開催途中、結果待機中も同じコマンドを再実行します。日付または開催場を明示する必要がある場合だけ、次を使います。

```bash
./race-day --date YYYY-MM-DD --venue 船橋
```

`race-day` は前日までのmeeting-aware history更新、当日全レースの静的preflight、immutable Primary day plan、既存collector、T15/fallbackの`race-shadow`、recommendation evidence、公式結果収集、`race-evaluate`、Actual Accountingを順に管理します。自動購入は行いません。Main BETを手動で実行した後は表示された `./race-purchase` の `PURCHASED` または `NOT_PURCHASED` commandを一度だけ実行し、同じ `./race-day` でPOST accountingを再開します。

新規day planのMain Recommendationは `P2_OPS_BET_POLICY_V2` で、WINのみを推奨・stake計算します。WIDEは研究専用で、MainのBET/NO_BET、stake、scopeには入りません。既存day manifestが保持する `P2_OPS_BET_POLICY_V1` はそのIDとSHA-256をauthorityとしてresumeし、V2へ変更しません。

大井 `OHI WIDE EXPERIMENTAL V0` は手動購入専用です。候補が存在しても、判定時点で予定発走まで300秒未満なら `NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED` として購入指示を出しません。300秒ちょうどは購入可能です。`SECONDS_TO_POST >= 480` は `COMFORTABLE_GE_8_MIN`、300秒以上480秒未満は `MARGINAL_5_TO_8_MIN`、300秒未満は `LATE_LT_5_MIN` です。表示される `SECONDS_TO_POST` と `ACTIONABILITY` は操作安全性の観測値であり、WIDEの科学的計算・Main推奨を変更しません。

凍結済み `wide_prospective_v1` と `win_prospective_v1` が有効な場合は、Main Recommendation Evidenceのcommit後に同じimmutable T15/fallback capture-setを使う研究shadowを起動します。WIN researchは `M0`（live calibrated Market）、`C0`（DEV-LIVE-V1）、`C1`（frozen shrinkage）を保存し、WIDE researchとは独立です。いずれも推奨、stake、BET/NO_BET、Main Evidenceを変更せず、失敗しても`ANALYSIS_READY`を止めません。研究predictionは発走前のみ作成し、発走後のbackfillはしません。T15とfallbackの評価は別集計です。

`P2_WIN_MARKET_TRAJECTORY_V1` は同じcollectorが保存済みのWIN Market captureを読むだけのresearch sidecarです。`T20/T15/T10/T05` はcollectorが明示記録したmarkだけを標準trajectoryとして保存し、`RECOVERY`は別扱いです。表示は `MARKET_TRAJECTORY: T20 ✓ ...` 程度で、Main表示を待たせません。結果・払戻を読まず、trajectory failureもMain/WIN/WIDE researchへ影響しません。

Trajectory / Lead-Lag observer の evidence race parent は、Main Evidence 作成前には未登録であり得ます。0件は `*_RACE_PARENT_PENDING` として正常待機し、1件で通常処理、2件以上だけが真の `*_RACE_NOT_UNIQUE` integrity failure です。

`NANKAN-P2-MKT-TRAJ-LL-V1` がfreeze済みの場合、船橋・大井のpost-freeze exact-T15 raceは別のconfirmatory cohortへ記録されます。通常表示はcohort enrollment、除外理由、残りN/clusterだけで、effect・sign・p-value・CIはfinal gateまで表示しません。

## 通常表示

- `RACE_DAY_READY`: 対象Primary race、最後の対象、次のT15を表示します。
- `ANALYSIS_READY`: 既存のT15またはpre-race fallback由来の推奨とEvidenceを表示します。
- `RECOMMENDATION_EXISTING`: 再起動後に既存Evidenceを再表示しただけで、新しい市場による再Decisionではありません。
- `RACE_CLOSED / SKIPPED_TOO_LATE`: 購入可能時間を過ぎ、Evidenceが無い正常なskipです。
- `DAY_WAITING_RESULTS_TIMEOUT`: 結果または必要な公式払戻が未完です。後で同じコマンドを実行するとPOST-RACEから再開します。
- `DAY_COMPLETE`: day plan内のPrimary targetだけを集計した日次レポートです。後続の非対象raceは待ちません。
- `ACTUAL_ACCOUNTING_PENDING`: 手動購入の明示confirmation待ちです。推薦・研究の科学的完了を失敗扱いにはしません。

`DAY_COMPLETE` の `WIN RESEARCH SHADOW` は確率研究専用であり、Main strategy P/Lには混ぜません。C1のlambda、Market calibration、Policyはprospective結果によって自動変更されません。

## CLI outcome / exit

通常終了時には最後に `RACE_DAY_OUTCOME` blockを一度表示します。exit `0` は健康な完了・正常待機・明示的なuser confirmation待ち、exit `10` は安全にresume可能な待機/block、exit `20` はimmutable evidence・model/policy・supervision契約の調査が必要なfailureです。`ACTUAL_ACCOUNTING_PENDING` は科学的な `DAY_COMPLETE` を失敗に変えません。詳細は [P2_RACE_DAY_OUTCOME_EXIT_CONTRACT_V1.md](P2_RACE_DAY_OUTCOME_EXIT_CONTRACT_V1.md) を参照してください。

## Latest verified live-day state

2026-09-02 大井の7 target (5R, 6R, 7R, 8R, 10R, 11R, 12R) は
`scientific_day_complete=true` と `actual_accounting_complete=true` を達成したが、
separate model-history readiness pending のため最終outcomeは
`DAY_COMPLETE_HISTORY_PENDING` / exit `10` / `BLOCKED_RECOVERABLE` /
`safe_to_resume=true` だった。これはclean exit-0 dayへ書き換えない。post-live
auditは [P2_OHI_20260902_POST_LIVE_AUDIT_023.md](../audit/reports/P2_OHI_20260902_POST_LIVE_AUDIT_023.md)
をauthorityとする。

## 安全境界

`PRE_RACE_CLOSED` までは対象日の結果ページ、払戻、result collector、`race-evaluate`を呼びません。全Primary targetがEvidence、too-late、または発走後のrace-scoped terminal blockになり、最後のPrimaryの予定発走時刻を過ぎて初めてPOST-RACEへ移ります。

同一日・同一場の実行はOSの`flock`で一つだけです。manifestは最初の静的preflight後にatomicに保存され、再起動時はhashを検証して再利用します。cardのmaterial metadataが計画と矛盾すれば`DAY_PLAN_CONFLICT`で停止します。取消など既存のruntime roster変化はplanの書換対象ではありません。

## 診断用CLI

`prospective_day_collector`、`prospective_collection_status`、`race-shadow`、`official_result_collector`、`race-evaluate`、legacy freeze CLIは診断・engineering・手動復旧用として残ります。通常手順では個別実行しません。
