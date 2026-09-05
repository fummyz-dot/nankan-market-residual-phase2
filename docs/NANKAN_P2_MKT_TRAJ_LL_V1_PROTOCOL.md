# NANKAN-P2-MKT-TRAJ-LL-V1

`STAGE_1_MARKET_LEAD_LAG` はoutcome-freeのconfirmatory protocolである。T05は後続市場のsoft benchmarkであり、真値でも払戻でもない。Lead-Lagは予測的な時間的関連であり、因果的情報流入、Probability Edge、Economic Edge、収益性を意味しない。

## Freeze と cohort

`models/development/nankan_p2_mkt_traj_ll_v1/protocol_manifest.json` がimmutable authorityである。V1 cohortは船橋・大井に限り、manifestの `protocol_frozen_at` より厳密に後のexact T15 captureだけを入口にする。既存のTrajectory/Lead-Lag raceは全て `PRE_FREEZE_POWER_PILOT` であり、移行・grandfatheringしない。

Primary engineering inclusionはexact `T15/T10/T05`、`T15_STANDARD` Main C0、同一active roster、frozen DEV-LIVE-V1、approved WIN price conversionを必要とする。fallback、RECOVERY、required mark重複、source conflict、roster changeは除外する。T20は保存するがPrimary membershipに不要である。

## 推定とgate

Primaryは船橋のbaseline-weighted WLS `m ~ u + z`、race-date clusterのRademacher wild-cluster bootstrap-tである。runner weightは `b`、one-sided alphaは0.025、二側95% CIも出す。通常accumulation中にはeffect、sign、p-value、CI、trendを出力しない。

- Funabashi: initial N=280、40 distinct `船橋 + race_date` clusters、18 calendar months、20 clustersで一度だけblinded N re-estimation。
- Ohi: initial N=656、40 distinct `大井 + race_date` clusters、36 calendar months、20 clustersで一度だけblinded re-estimation。ただし船橋の `EXISTENCE_SUPPORTED` までeffect outputをsealする。

`beta_min=0.20` はpractical progression thresholdでありnull boundaryではない。`CI_lower>0` は `EXISTENCE_SUPPORTED`、`CI_lower>0.20` は `DECISION_GRADE`、`CI_upper<0.20` は `PRACTICALLY_RELEVANT_EFFECT_RULED_OUT`、その他は `INCONCLUSIVE` である。Stage 2は船橋 `DECISION_GRADE` 後にも自動開始しない。

## Data boundary

V1 evidenceはresult、finish、payout、settlement、P/L、ROIを読まない。推定前のrendererはenrollment、exclusion、race-date clusters、remaining N、calendar status、protocol/source hashesのみを表示する。
