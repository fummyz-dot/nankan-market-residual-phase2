# P2 Actual Purchase Accounting V1

`P2_ACTUAL_PURCHASE_EVIDENCE_V1` は、手動購入の事実だけを記録する cash-accounting contract である。推薦stake、recommended-strategy settlement、研究P/Lとは別の namespace とし、自動購入・注文送信・off-policy bet は扱わない。

## Authority と immutable evidence

- Main は committed `P2_RECOMMENDATION_EVIDENCE_V1` の `recommendation_id + ticket_index` のみを source とする。`WIN` と `WIDE` のみ対応し、TRIOは拒否する。
- Main action は `outputs/live_development/actual_purchase_evidence_v1/YYYY-MM-DD/` に一ticket一fileで保存する。canonical payload SHAからevidence IDを作り、同一actionは idempotent、status/stake/placed time/execution odds の変更は conflict fail-closed である。
- Experimental WIDE は既存の Funabashi/Ohi V0 intent と、raw intent SHA boundの明示confirmationを source とする。過去confirmationを新namespaceへ複写しない。
- status は `PURCHASED` または `NOT_PURCHASED` の最終一回だけである。`NOT_PURCHASED` は stake 0、settlement対象外であり、absenceから推測しない。
- 同一race・ticket type・canonical combination を異なるsourceがともに `PURCHASED` と主張した場合は `ACTUAL_PURCHASE_DUPLICATE_ECONOMIC_TICKET_SOURCE_CONFLICT` で停止する。

## Main confirmation

```bash
./race-purchase --recommendation-id '<ID>' --ticket-index 1 --confirm-purchased --use-recommended-stake
./race-purchase --recommendation-id '<ID>' --ticket-index 1 --confirm-not-purchased
```

購入時は recommended stake または100円単位の明示整数stakeを一つだけ選ぶ。`placed_at` と `execution_odds` は任意のprovenanceで、official return計算には使わない。confirmationはscheduled postより前でなければならず、`placed_at` がある場合はconfirmation以前かつpost以前である。Main evidenceは既存Recommendation payload SHAとcommit済みticketを検証してからのみ作成する。

## Settlement と reports

購入済みWIN/WIDEは既存の official `RESULT_OFFICIAL_FINAL` と canonical payout key を使い、`outputs/live_development/actual_purchase_settlements_v1/YYYY-MM-DD/` にrace-level immutable settlementとして保存する。official payout/refund/dead-heat/source-change semanticsは `P2_SETTLEMENT_EVAL_V1` をそのまま再利用する。legacy `actual_bets` は読まず書かず、migrateもしない。

日次reportは `outputs/live_development/YYYY-MM-DD/actual_purchase_evaluation_<venue>.json` に生成する。`COMPLETE` は全Main BET ticketと適用Experimental manual-buy actionの明示confirmation、および全購入ticketのofficial settlementを必要とする。欠けたconfirmationは `PENDING_CONFIRMATION`、official settlement待ちは `SETTLEMENT_WAITING`、integrity conflictは `ERROR` である。cash turnoverは購入済みactual stakeのみ、gross payoutはofficial returnのみ、net profitは gross minus turnover、ROIとrecoveryはturnoverが0ならnullである。

累積reportは `outputs/live_development/accounting/actual_purchase_cumulative_v1.json` で、日次reportからdeterministicに再構築する。scope startは `2026-09-01`。Implementation-017は9/1をretroactively importしないため、その日は `INCOMPLETE_HISTORY` coverage gapとして保持する。

## Race-day POST

POSTは既存のrecommended settlementとresearch evaluationの後にActual Accountingを実行する。`PENDING_CONFIRMATION` は科学的な `DAY_COMPLETE` を止めず、`ACTUAL_ACCOUNTING_PENDING` と未確認actionを表示する。`SETTLEMENT_WAITING` は既存POST waitを使い、`ERROR` はfail-closedでActual completeを主張しない。購入confirmation後は同じ `./race-day` の再実行でderived reportを再構築する。

## Bounded retroactive migration

通常の `./race-purchase` とExperimental confirmation CLIは `LIVE_EXPLICIT_USER` のpost-deadline guardを維持する。例外は `P2-ACTUAL-BET-RETROACTIVE-IMPORT-20260901-018` の専用moduleだけであり、2026-09-01 大井の承認済み三actionを `RETROACTIVE_USER_CONFIRMED` としてimportする。これは一般的なretroactive CLIではない。WINのhistorical `placed_at` と`execution_odds`は不明ならnullのまま保持し、import timestampをhistorical purchase timeとして扱わない。
