# P2_CURRENT_PROSPECTIVE_V1

通常運用で追加コマンドは不要です。`./race-day` は、Main Recommendation Evidence のcommit後に、同じ不変CURRENT snapshotからCURRENT research evidenceを非同期で作成します。

これは `P2_CURRENT_PROSPECTIVE_V1` の研究ledgerです。馬体重・増減、公式同一rowの `/kis_info/<id>.do` 騎手ID、strict as-of の前走騎手ID、取消・active rosterを保存します。DEV-LIVE-V1、FS04、Policy V2、買い目、stake、Recommendation Evidenceには入力しません。

T15_STANDARD は `PRIMARY_T15`、PRE_RACE_FALLBACK は `SECONDARY_FALLBACK` として分離します。発走前にMainが採用したCURRENT snapshotがなければ `CURRENT_RESEARCH_MISSED` を残し、結果後にcardを再取得してprospective recordをbackfillしません。

騎手の `SAME` / `CHANGED` は、currentと最新strict-prior startの両方で公式IDが安全に存在する場合だけ決めます。表示名比較、pedigree・隣接cell・Keibabook・辞書のfallbackはありません。前走が無ければ `NO_PRIOR_START`、ID不明なら `UNKNOWN` です。

`declared_field_size` は、現CURRENT cardがactive starter数しか安全に表さないためnullです。初回出走登録数などを代用せず、`field_size_delta` もnullとして記録します。
