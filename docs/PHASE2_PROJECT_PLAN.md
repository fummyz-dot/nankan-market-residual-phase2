---
project_id: NANKAN_MARKET_RESIDUAL_PHASE2
project_name: 南関競馬・中荒れ市場残差分析 Phase 2
version: 0.1.0
created_at: 2026-08-18
status: DRAFT_FOR_PREREGISTRATION
source_dossier: NAR_ONLY_GIVEUP_DOSSIER.md
supersedes_v1: false
---

# 南関競馬・中荒れ市場残差分析 Phase 2
## 新Project方針書・実行計画・事前登録フレームワーク

> **本Projectの立場**  
> Phase 2は、NAR-only V1を同じholdout上で救済する作業ではない。  
> 新しい仮説、データ時点、feature contract、objective、model-search budget、未使用future holdoutを持つ、独立した研究Projectとして実施する。

---

## 0. この文書の使い方

この文書は、Phase 2を開始するための以下4つの役割を兼ねる。

1. Project Charter：何を目的とし、何を目的としないかを固定する。
2. Research Protocol：仮説、データ、モデル、評価順序を固定する。
3. Implementation Plan：作業をどの順序で進めるかを定義する。
4. Preregistration Template：future holdoutを開く前に確定すべき項目を明示する。

本文では、項目を次の4種類に分ける。

| ラベル | 意味 |
|---|---|
| `INHERITED` | V1から引き継ぐ研究上の原則 |
| `RECOMMENDED` | Phase 2で採用を推奨する初期方針 |
| `FREEZE_REQUIRED` | future holdout開始前に値・ルールを固定する項目 |
| `EXPLORATORY_ONLY` |探索には使用できるが、主たるconfirmatory claimには使用しない項目 |

`FREEZE_REQUIRED`の項目は、結果ラベルを見た後に変更してはならない。変更が必要な場合は、Protocol Amendmentを発行し、変更後に新たな未使用holdoutを設定する。

---

# Part I. Project Charter

## 1. V1から引き継ぐ事実

### 1.1 V1の結論

V1の結論は、次の限定された範囲に適用される。

> 事前登録したNAR-only V1探索空間では、市場に対する再現可能な追加Edgeを確認できなかった。

これは、NARデータ一般、南関競馬一般、または競馬予測一般にEdgeが存在しないという普遍的結論ではない。

### 1.2 V1で確認できたこと

- WIN、WIDE、TRIOの3券種すべてでUniformより予測性能が良かった。
- Fold間で大きく崩れず、strict as-ofのNAR情報に予測信号は存在した。
- ただし、Popularityまたはnormalized market benchmarkより弱かった。
- fixed 75/25 Market + Model blendは、Market単独を改善しなかった。
- 全4会場で改善方向を確認できなかった。
- CORE temporal confirmationでも事前登録基準を満たさなかった。

### 1.3 V1のmarket confirmation

| 券種 | V1のMarket追加結果 | 解釈 |
|---|---:|---|
| WIN | Blend - Market LL = `+0.008982` | Market単独より悪化 |
| WIDE | Blend - Market CE = `+0.016351` | Market単独より悪化 |
| TRIO | Blend - Market LL = `+0.051735` | Market単独より悪化 |

### 1.4 V1で未検証だった主要仮説

Phase 2で検討対象になり得るのは、V1で意図的に未検証とされた以下の領域である。

- actual pre-race odds snapshots
- odds movement / market trajectory / late money
- probability calibration
- market residualを直接targetにするmodel
- speed figure / standard time / going-adjusted speed
- corner position / pace / first-3F / last-3F
- race class / condition情報の完全化
- scheduled post timeと当日course bias
- WIDE lower-upper rangeの利用
- pairwise / setwise dependenceを明示したjoint probability model
- horse historyのsequence representation
- jockey / trainer / sire identity representation
- Keibabook等の独立情報源

### 1.5 V1期間の扱い

`INHERITED`

- 2026-07以前のデータは、V1結果を確認済みである。
- したがって、その期間をPhase 2の最終future holdoutとして扱ってはならない。
- 2026-07以前は、再現、データ工学、feature開発、モデル選択、power simulationのためのdevelopment dataとしてのみ使用できる。

---

## 2. Phase 2の目的

### 2.1 最終目的

南関競馬の大井・船橋・川崎・浦和において、購入判断時点で観測可能な情報のみを用い、次を再現可能に実証する。

> **Market単独の確率予測を改善し、その改善が事前登録された中荒れ対象で経済的価値につながるか。**

### 2.2 Phase 2で分けて判定する3段階

Phase 2では、「当たる」「市場より良い」「利益になる」を混同しない。

| 段階 | 問い | 主な判定 |
|---|---|---|
| Probability | 結果確率を予測できるか | Uniform等との比較 |
| Incremental Edge | Marketより確率予測を改善するか | Candidate - Marketのpaired loss差 |
| Economic Edge | 購入可能価格で利益余地があるか | prospective ROI、expected value、slippage耐性 |

最終的な成功判定には、少なくともIncremental Edgeの確認が必要である。予測精度がUniformより良いだけでは成功としない。

### 2.3 Phase 2の中心仮説

> 購入判断時点のMarketをbaselineとして固定し、NAR履歴、speed、pace、class、course bias、およびmarket trajectoryから「Marketがどの方向にどれだけ誤っているか」を直接学習すれば、Market単独を再現可能に改善できる可能性がある。

---

## 3. Projectの非目的

以下はPhase 2の目的ではない。

- V1の失敗を見かけ上取り消すこと。
- 同じ119 featuresのまま、アルゴリズムを多数試して偶然の改善を探すこと。
- V1 holdoutを再利用してvenue、odds帯、threshold、blend比率を救済探索すること。
- 的中率だけを最大化すること。
- 最終oddsを購入時点oddsとして扱うこと。
- final holdoutを見た後にfeature、model、thresholdを修正すること。
- 一つの好成績venueや一つの期間だけを選んで成功と主張すること。
- 研究結果をそのまま実運用収益の保証とみなすこと。

---

## 4. 成功の定義

### 4.1 Primary Success

`FREEZE_REQUIRED`

券種ごとに事前登録された単一のPrimary Candidateが、未使用future holdoutのALL scopeで、対応するCalibrated Market baselineよりrace-level lossを改善すること。

基本判定は以下とする。

\[
\Delta = L_{candidate} - L_{calibrated\ market}
\]

- `PASS`: \(\Delta < 0\) かつ、race-date block bootstrapによる95%信頼区間の上限が0未満。
- `INCONCLUSIVE`: 点推定は負だが、95%信頼区間が0を跨ぐ。
- `REJECT`: 点推定が0以上、または事前登録されたminimum practical effectを満たさない。

### 4.2 Secondary Success

Primary Successを満たした場合に限り、以下を評価する。

- pre-registered CORE帯でMarket期待を上回る。
- consecutive time blocksで改善方向が安定する。
- venue別の符号が極端に不整合でない。
- calibrationが悪化していない。
- conservative odds haircut後の期待値が正である。
- prospective flat-stake ROIが事前登録基準を満たす。

### 4.3 最終Projectステータス

| Status | 意味 |
|---|---|
| `PHASE2_DATA_NOT_READY` | 時点付きmarket dataが研究品質に達しない |
| `PHASE2_MARKET_BASELINE_READY` | raw/calibrated market benchmarkが確立 |
| `PHASE2_PROBABILITY_EDGE_CONFIRMED` | Marketに対する確率改善をfuture holdoutで確認 |
| `PHASE2_ECONOMIC_EDGE_INCONCLUSIVE` | 確率改善はあるが収益性は未確認 |
| `PHASE2_ECONOMIC_EDGE_CONFIRMED` | prospective経済評価も通過 |
| `PHASE2_REJECT` | 事前登録CandidateがMarketを改善しない |
| `PHASE2_GIVEUP` | 許容した新仮説空間を使い切り、追加Edgeを確認できない |
| `READY_FOR_LIMITED_LIVE_PILOT` | 統計・経済・運用の全ゲートを通過 |

---

# Part II. Research Hypotheses

## 5. 仮説階層

Phase 2は以下の順で仮説を検証する。各仮説は独立したfeature / objective変更理由を持つ。

### H0: Market calibration仮説

Raw normalized marketには、race内分布の過度な尖り・平坦化等の単純なcalibration errorが存在し得る。

\[
q_i^{(\gamma)} = \frac{q_i^\gamma}{\sum_j q_j^\gamma}
\]

- \(\gamma=1\): raw market
- \(\gamma<1\): 分布を平坦化
- \(\gamma>1\): 分布を尖らせる

目的は、NAR特徴量を加える前に最善のMarket-only baselineを確立することである。

### H1: Legacy residual仮説

V1の119 runner-level featuresは、レースをゼロから予測する用途ではMarketより弱かったが、Marketの小さな誤差だけを学習する用途では追加情報を持つ可能性がある。

### H2: Racing information residual仮説

V1で欠落していた以下の情報が、Marketに対する追加情報を持つ可能性がある。

- strict as-of speed
- pace / corner / sectional
- class / condition completeness
- scheduled post time
- same-day course bias
- pre-race body weight等、購入判断時点までに公表済みの情報

### H3: Market trajectory仮説

単一時点oddsだけではなく、複数snapshot間の変化、変化速度、late money、market entropy等が、最終的な市場誤差または実行価格リスクを説明する可能性がある。

### H4: Joint probability仮説

WIDE / TRIOをpair・comboごとに独立に直接分類するより、着順またはtop-3集合を生成するjoint probability modelの方が、馬間依存と券種間整合性を表現できる可能性がある。

### H5: External information仮説

Keibabook調教・能力表等の外部情報は、NAR + Marketとは異なる追加情報を持つ可能性がある。

`RECOMMENDED`: H5はPhase 2本体に混ぜず、`Phase 2X`として独立したincremental-value experimentにする。

---

## 6. 検証順序

### 6.1 券種の順序

1. WIN
2. WIDE
3. TRIO

理由:

- WINはMarketとの差が最も小さく、確率・odds・払戻の対応が比較的明確。
- WIDEはjoint top-3 modelの効果を検証しやすい。
- TRIOは組合せ数が多く、V1のMarket悪化幅も最大で、分散が高い。

### 6.2 モデルの順序

1. Uniform
2. Raw Market
3. Calibrated Market
4. Market-offset + Legacy features
5. Market-offset + New racing features
6. Market-offset + Market trajectory
7. Joint ranking / top-3 model
8. External source incremental model

順序を逆転させてはいけない。特に、Calibrated Marketを確立する前に複雑なモデルを成功判定してはならない。

---

# Part III. Data Strategy

## 7. データアーキテクチャ

### 7.1 Source of Truth

`INHERITED`

raw sourceは変更せず、取得時のバイト列をSource of Truthとして保存する。

推奨構成:

```text
data/
  raw/
    nar_race/
    nar_odds/
    odds_snapshots/
    results/
    payouts/
    external/
  manifests/
    sha256_manifest.csv
    source_inventory.csv
  staging/
  curated/
  feature_store/
  holdout/
```

各raw objectに最低限以下を記録する。

- source name
- source URLまたは取得元識別子
- downloaded_at
- content timestamp
- SHA-256
- parser version
- timezone
- license / usage note

### 7.2 Database分離

以下を論理的・物理的に分離する。

| DB | 内容 | 主な用途 |
|---|---|---|
| `history_db` | 過去レース、馬、騎手、調教師、過去走 | strict as-of features |
| `market_snapshot_db` | 購入判断時点以前のodds snapshots | baseline、movement、execution |
| `outcome_db` | 着順、確定結果、払戻 | label、realized return |
| `feature_store` | availability監査済み特徴量 | model input |
| `audit_db` | exclusion、join、run manifest | 再現性・監査 |

model training用datasetを作るときに、`outcome_db`の列がfeature側へ混入しない構造を採用する。

---

## 8. 時刻の契約

### 8.1 必須timestamp

すべてAsia/Tokyoのtimezone-aware timestampとして保持し、必要に応じUTCも併記する。

- `scheduled_post_time`
- `actual_post_time`（取得できる場合）
- `market_published_at`
- `snapshot_captured_at`
- `body_weight_published_at`
- `weather_observed_at`
- `track_condition_published_at`
- `result_official_at`
- `payout_official_at`

### 8.2 Availability rule

あるfeature \(x\) を購入判断時点 \(\tau\) で使用できる条件は、原則として次である。

\[
available\_at(x) \le \tau
\]

イベントが過去に発生していても、公表時刻が \(\tau\) より後なら使用禁止とする。

### 8.3 Same-day course bias

同一日の過去レースを使う場合、race numberではなく、実際に結果が確定または安全に観測可能になった時刻で判定する。

\[
result\_official\_at(r_{past}) < \tau(r_{current})
\]

この条件を満たさない当日レース情報は使用しない。

### 8.4 時刻不明データ

`MARKET_TIME_UNKNOWN`、公表時刻不明、取得順序のみ判明等のデータは、以下のいずれかに分類する。

- development reference only
- feature禁止
- sensitivity analysis only

時刻不明データをconfirmatory holdoutの購入可能情報として扱わない。

---

## 9. Actual pre-race odds snapshot

### 9.1 最優先要件

Phase 2のconfirmatory claimには、actual pre-race snapshotを原則必須とする。

snapshotが用意できない場合に可能なのは、以下までである。

- model engineering
- historical feature ablation
- final-like marketとのdevelopment comparison
- data pipeline検証

snapshotなしで実購入可能Edgeを主張してはならない。

### 9.2 Primary decision time

`FREEZE_REQUIRED`

Project開始後、結果ラベルを見ずにcollection feasibilityを監査し、主たる購入判断時点 \(\tau_0\) を一つだけ固定する。

初期推奨候補:

- `scheduled_post_time - 5 minutes`
- 運用遅延が大きい場合は `scheduled_post_time - 10 minutes`

Primary timeを決めた後、他の時点はsecondaryまたはexploratoryとする。

### 9.3 Secondary snapshots

`RECOMMENDED`

可能なら以下の複数時点を保存する。

- T-30
- T-15
- T-10
- T-5
- T-2
- last snapshot before close

ただし、Primary confirmatory testに使用する時点は一つに固定する。

### 9.4 snapshot record

最低限、以下を保存する。

- race_id
- ticket_type
- runner / pair / combo identifier
- displayed odds
- WIDE lower odds
- WIDE upper odds
- scheduled post time
- capture time
- source publication time（取得可能なら）
- race status
- scratching status
- field size
- collector version
- response hash
- capture success / error code

### 9.5 snapshot quality gate

以下は結果ラベルを見ず、collection auditだけで固定する。

`RECOMMENDED default`:

- Primary時点のcoverage: ALLで97%以上
- venue別coverage: 各95%以上
- race / runner join mismatch: 0
- duplicate primary key: 0
- capture clock drift: p99で30秒未満
- Primary時点に対するsnapshot staleness: 60秒以内
- scratch反映ルール: 事前に固定

基準を満たさない場合は、`PHASE2_DATA_NOT_READY`とし、confirmatory clockを開始しない。

### 9.6 odds movement features

Primary時点より前のsnapshotだけを用いて以下を生成できる。

- log odds change
- implied probability change
- rank change
- momentum / acceleration
- market entropy change
- overround / normalized mass change
- late concentration
- WIDE range width and change
- missing / suspended quote indicator

Primary時点後のsnapshotはfeatureに使用しない。

---

## 10. Dataset splitとfuture holdout

### 10.1 Development period

- 2026-07以前: development only
- V1で使用したFold4 / market confirmation期間: development only
- V1結果を知っているため、Phase 2最終confirmには使用禁止

### 10.2 Prospective collection stabilization period

新snapshot collector開始直後は、データ品質・時刻精度・欠損原因の確認に使用する。

この期間は結果ラベルを使ったfeature / model選択に利用しないか、利用する場合はその時点でdevelopment periodへ降格させ、future holdoutから除外する。

### 10.3 Model-selection period

`FREEZE_REQUIRED`

prospective dataの一部をmodel selectionに使用する場合は、その期間を明記し、final holdoutから除外する。

### 10.4 Final untouched future holdout

`RECOMMENDED default`:

- preregistrationとmodel freezeの後に開始
- 連続したcalendar period
- 最低12か月または3,000 eligible racesの遅い方まで
- power analysisがより大きいsampleを要求する場合は、その値を採用
- 中間成績をmodel developerへ開示しない

`FREEZE_REQUIRED`:

- holdout開始日時
- holdout終了条件
- eligible race rule
- exclusion rule
- primary ticket type
- primary decision time
- primary candidate hash

### 10.5 Holdout custody

推奨方法:

- outcome labelを別権限で保持する。
- model developerには予測提出後までlabelを見せない。
- prediction fileをhash付きで提出する。
- race終了後の上書きを禁止する。
- dailyまたはmeeting単位でprediction manifestを確定する。

---

# Part IV. Feature Plan

## 11. Feature namespace

Phase 2では、V1 featureと新featureを明確に分離する。

| Namespace | 内容 | Phase 2での役割 |
|---|---|---|
| `V1_F0-F8` | V1の119 runner features | legacy residual baseline |
| `P2_SPD` | speed / standard time | 新規仮説 |
| `P2_PACE` | corner / pace / sectional | 新規仮説 |
| `P2_CLASS` | class / condition | 新規仮説 |
| `P2_BIAS` | same-day course bias | 新規仮説 |
| `P2_CURRENT` | body weight等の当日情報 | 購入時点で公開済みの場合のみ |
| `P2_MKT` | odds snapshot / movement | market residual / execution |
| `P2_ID` | jockey / trainer / sire identity | 二次候補 |
| `P2_SEQ` | horse history sequence | 後段候補 |
| `P2_EXT` | Keibabook等 | Phase 2Xのみ |

---

## 12. Feature contract必須項目

各featureに以下を記載する。

```yaml
feature_name: example_feature
namespace: P2_SPD
entity: runner
source_columns: []
event_time_column: null
available_time_column: null
lookback_rule: null
aggregation: null
missing_value_rule: null
cold_start_rule: null
same_day_rule: null
leakage_risk: null
unit_tests: []
owner: null
status: DRAFT
```

### 12.1 禁止事項

- label期間全体からstandard timeを作り、過去へ逆流させる。
- future raceを含む騎手・調教師集計。
- race後に確定するbody weight、馬場、結果を事前情報として使用する。
- field内の最終人気順位を入力する。
- final oddsをPrimary snapshotの代わりに入力する。
- missingnessを結果情報で埋める。

---

## 13. Speed feature

### 13.1 基本形

単純な最終タイムではなく、会場、距離、馬場、class、時期等で調整したstrict as-of standard timeを作る。

例:

\[
speed_{r} = -\frac{time_r - \widehat{standard\_time}_{asof}}{\widehat{scale}_{asof}}
\]

### 13.2 推奨構成

- raw final time
- distance-normalized time
- venue × distance standard time
- going-adjusted speed
- class-adjusted speed
- pace-adjusted final time
- recent 1 / 3 / 5走のlevel、trend、dispersion
- uncertainty / sample count
- layoffによるdiscount

### 13.3 as-of実装

standard time推定に使用できるのは、その過去走日より前に利用可能だったraceだけとする。

疎なセルは階層的縮約を行う。

```text
venue × distance × going × class
  -> venue × distance × going
  -> venue × distance
  -> distance band
  -> global
```

後から最も成績の良い縮約階層を選ばない。候補数と選択規則を事前登録する。

---

## 14. Pace / corner feature

### 14.1 runner-level

- first-3F / last-3F
- corner positions
- position gain / loss
- pace position relative to field
- early speed rating
- closing speed rating
- front-run / stalk / mid / close tendency
- pace consistency

### 14.2 race-set level

- 逃げ候補数
- 先行候補数
- early-speed上位馬の集中度
- 同型競合
- 内外枠と脚質の組合せ
- projected pace pressure
- field pace entropy
- favouriteとpace advantageの関係

### 14.3 注意点

paceはrunnerを独立に採点するだけでは不十分である。race内の他馬構成により意味が変わるため、within-race transformまたはsetwise interactionを使う。

---

## 15. Class / condition feature

- race class codeの正規化
- class up / down
- purse / condition proxy
- age / sex condition
- weight condition
- distance switch
- surface / venue switch
- condition eligibility
- class内の相対能力
- class変換表のversion

class体系変更がある場合、変換ルールを時期別にversion管理する。

---

## 16. Same-day course bias

購入判断時点までに終了した当日レースだけから、以下を逐次更新する。

- inner / outer advantage
- front / close advantage
- distance-specific clock level
- going evolution
- gate bias
- pace × position outcome

### 16.1 Cold start

当日最初のレースやsample不足時は、前開催・直近期間のpriorへ縮約する。

### 16.2 安全策

- race numberだけで先後関係を決めない。
- result official timeが不明なら当日biasを無効化する。
- 当日後半の結果を当日前半へ逆流させないunit testを作る。

---

## 17. Current pre-race information

V1で禁止したcurrent body weight等も、購入判断時点までに公表済みであればPhase 2では利用可能である。

候補:

- body weight
- body weight change
- jockey change
- equipment / shoe等、取得可能で時点が安全な情報
- weather
- track condition
- scratch / field-size change

各情報について`published_at <= decision_time`を必須とする。

---

## 18. Identity / sequence features

`RECOMMENDED`: speed、pace、class、biasの検証後に着手する。

### 18.1 Identity

最初から大規模embeddingを使わず、以下をbaselineにする。

- smoothed rolling rate
- regularized target encoding
- dynamic rating
- sample count / uncertainty

### 18.2 Horse sequence

sequence modelの前に、以下の強い表形式baselineを作る。

- recent N race vectors
- time-decayed aggregation
- trend / volatility
- condition similarity
- state-space rating

sequence modelは、表形式baselineをdevelopmentで明確に上回った場合にのみPrimary candidate候補へ進める。

---

# Part V. Market Baseline and Models

## 19. Market normalization

WIN / TRIOの基本形:

\[
q_i = \frac{1/o_i}{\sum_j 1/o_j}
\]

WIDEは3本の的中pairを持つため、normalized ticket massとして扱う。lower-only、upper情報を含む方式等の候補は、少数の事前登録候補からdevelopmentのみで一つを選ぶ。

### 19.1 Market baseline候補

`FREEZE_REQUIRED`:

- WIN: inverse snapshot odds
- TRIO: inverse snapshot odds
- WIDE primary candidate set:
  - lower-only inverse odds
  - geometric-mean odds
  - interval-width補正

WIDE baseline候補数を増やしすぎない。推奨上限は3方式。

---

## 20. Calibrated Market

券種ごとにtemperature / power calibrationを行う。

\[
q_i^{(\gamma)} = \frac{q_i^\gamma}{\sum_j q_j^\gamma}
\]

### 20.1 選択

- \(\gamma\) はdevelopment foldsだけで推定する。
- final holdoutで再推定しない。
- venue別gammaはPrimaryにしない。
- ALLで一つのgammaをPrimaryとする。
- venue別はsecondary diagnosticに限定する。

### 20.2 Market-only追加候補

候補数を固定した上で、必要なら以下を比較できる。

- one-parameter power calibration
- monotonic calibration
- field-size conditioned calibration

最終的なPrimary Market baselineは一つだけに固定する。

---

## 21. Market-offset residual model

### 21.1 WIN

推奨Primary architecture:

\[
p_i(\tau) =
\frac{
q_i(\tau)^\gamma \exp(f_\theta(x_i, R))
}{
\sum_j q_j(\tau)^\gamma \exp(f_\theta(x_j, R))
}
\]

- \(q_i(\tau)\): Primary decision timeのMarket
- \(\gamma\): developmentで固定したmarket calibration
- \(x_i\): runner features
- \(R\): race-set features
- \(f_\theta\): Marketからの残差修正

\(f_\theta=0\)ならCalibrated Marketへ戻る。

### 21.2 学習objective

winnerに対するrace-level negative log likelihoodを使用する。

\[
L_r = -\log p_{winner}
\]

### 21.3 Residual shrinkage

Marketから大きく逸脱しすぎないよう、以下を事前登録候補とする。

- L2 regularization
- tree depth制限
- residual score clipping
- posterior shrinkage
- early stopping

結果後に最適なclipping値を探さない。候補数を固定する。

### 21.4 Legacy residual gate

V1の119 featuresだけを使用するMarket-offset modelを最初に評価する。

目的:

- V1 featuresが残差用途でも無効かを切り分ける。
- 新featureの改善を正しく測るbaselineを作る。

Legacy residualが失敗しても、H2は独立仮説として事前登録済みなら続行できる。ただし、H1失敗を隠してH2と一括成功扱いにしない。

---

## 22. WIDE / TRIOのticket-level residual model

最初のbaselineは、券種Marketを直接offsetにする。

WIDE:

\[
p_h = \frac{q_h^\gamma \exp(f_\theta(z_h, R))}{\sum_k q_k^\gamma \exp(f_\theta(z_k, R))}
\]

TRIO:

\[
p_c = \frac{q_c^\gamma \exp(f_\theta(z_c, R))}{\sum_k q_k^\gamma \exp(f_\theta(z_k, R))}
\]

- \(h\): unordered pair
- \(c\): unordered 3-horse combination
- \(z\): constituent runner featuresとpair/combo interaction

これは、V1のfixed 75/25 blendとは異なり、Market残差をobjective内で直接学習する。

---

## 23. Joint ranking / top-3 model

### 23.1 基本モデル

各runnerにutility \(u_i\) を付与し、Plackett-Luce型にtop orderを生成する。

\[
P(\pi_1,\pi_2,\pi_3) =
\frac{e^{u_{\pi_1}}}{\sum_j e^{u_j}}
\frac{e^{u_{\pi_2}}}{\sum_{j\ne\pi_1} e^{u_j}}
\frac{e^{u_{\pi_3}}}{\sum_{j\notin\{\pi_1,\pi_2\}} e^{u_j}}
\]

Market residualを入れる場合:

\[
u_i = \alpha\log q_{win,i} + g_\theta(x_i,R)
\]

### 23.2 WIDE probability

pair \(\{i,j\}\) がtop-3に同時に入る確率は、第三のrunnerと順序を合計する。

\[
P(\{i,j\}\subset Top3)
= \sum_{k\ne i,j}\sum_{\pi\in Perm(i,j,k)} P(\pi_1,\pi_2,\pi_3)
\]

### 23.3 TRIO probability

combo \(\{i,j,k\}\) がtop-3を占める確率は、6通りの順序を合計する。

\[
P(\{i,j,k\}=Top3)
= \sum_{\pi\in Perm(i,j,k)} P(\pi_1,\pi_2,\pi_3)
\]

### 23.4 ticket marketとの接続

joint model単独をMarketと比較するだけでなく、joint probabilityをticket-level residualの入力にする。

例:

\[
p_h \propto q_{wide,h}^{\gamma}
\exp\left(\beta\log p_{joint,h} + r_\theta(z_h,R)\right)
\]

\[
p_c \propto q_{trio,c}^{\gamma}
\exp\left(\beta\log p_{joint,c} + r_\theta(z_c,R)\right)
\]

これにより、WIDE / TRIO Marketをbaselineに保ちつつ、着順構造から得られる情報を加える。

### 23.5 モデル複雑化の順序

1. simple Plackett-Luce
2. tree-generated utility + Plackett-Luce
3. small permutation-invariant set encoder
4. mixture / correlated latent performance

巨大なsequence / Transformerから開始しない。

---

## 24. Setwise interaction

raceはrunner順序に意味のない集合である。neural modelを使う場合、入力順に依存しない構造を採用する。

候補:

- DeepSets型aggregation
- small Set Transformer
- attention over runners
- pairwise pace interaction layer

必須unit test:

- runner順序をshuffleしても同じ確率になる。
- scratch後に再正規化される。
- field size変化でNaN / overflowが出ない。

---

# Part VI. Search Budget and Experiment Control

## 25. Model-search budget

`FREEZE_REQUIRED`

同じ情報集合で大量のalgorithm searchをしない。

推奨上限:

| Block | 最大候補数 | 備考 |
|---|---:|---|
| Market calibration | 3 | 最終baselineは1つ |
| WIN legacy residual | 6 | GBDT中心 |
| WIN new-feature residual | 6 | feature block比較を含む |
| WIDE ticket residual | 4 | direct residual |
| WIDE joint model | 4 | simpleから開始 |
| TRIO ticket residual | 4 | direct residual |
| TRIO joint model | 4 | WIDE知見を流用可 |
| Set encoder | 4 | 前段gate通過時のみ |
| External Phase 2X | 別budget | 本体と分離 |

### 25.1 Primary candidate数

future holdoutへ提出するPrimary Candidateは、券種ごとに1つだけとする。

Challengerを提出する場合も、事前登録された最大2つまでとし、Primaryとの階層を明示する。

### 25.2 Hyperparameter tuning

- nested walk-forward内だけで実施する。
- fold averageのPrimary metricで選ぶ。
- best foldだけで選ばない。
- venue-specific tuningをしない。
- final holdoutでearly stoppingしない。
- random seedを含むrun manifestを保存する。

---

## 26. Feature ablation budget

新featureはブロック単位で追加する。

推奨順:

1. Legacy only
2. + Speed
3. + Pace
4. + Class
5. + Course bias
6. + Current pre-race
7. + Market trajectory
8. Full approved set

各ブロック内で個別featureを何十通りも組み替えない。個別feature削除は、data qualityまたはleakage理由がある場合に限る。

---

## 27. Protocol Amendment

結果を見た後に以下を変更する場合は、新しい仮説として扱う。

- feature追加
- feature定義変更
- decision time変更
- CORE帯変更
- threshold変更
- venue限定
- model family追加
- search budget追加
- objective変更
- exclusion rule変更

Amendmentには以下を記録する。

```yaml
amendment_id: P2-A001
requested_at: null
reason: null
information_seen: null
changed_items: []
affected_period: null
new_holdout_required: true
reviewer_approval: null
```

---

# Part VII. Evaluation Protocol

## 28. Primary metrics

### 28.1 WIN

Race Log Loss:

\[
LL_r = -\log p_{winner}
\]

Primary comparison:

\[
\Delta_{WIN} = mean(LL_{candidate,r} - LL_{market,r})
\]

### 28.2 WIDE

3本の的中pair集合 \(H_r\) に対するPair Cross Entropy:

\[
CE_r = -\frac{1}{3}\sum_{h\in H_r}\log p_h
\]

Primary comparison:

\[
\Delta_{WIDE} = mean(CE_{candidate,r} - CE_{market,r})
\]

### 28.3 TRIO

Exact Combination Log Loss:

\[
LL_r = -\log p_{correct\ combo}
\]

Primary comparison:

\[
\Delta_{TRIO} = mean(LL_{candidate,r} - LL_{market,r})
\]

---

## 29. Secondary probability metrics

- Brier score
- calibration slope / intercept
- reliability diagram
- expected calibration error
- top-1 accuracy
- top-3 coverage
- ranking correlation
- entropy / sharpness
- probability mass assigned to actual winning set

Secondary metricでのみ改善した場合、Primary Successとはしない。

---

## 30. Statistical inference

### 30.1 Bootstrap unit

calendar race_date block bootstrapを使用する。meetingや同日馬場状態による相関を保つ。

`RECOMMENDED default`:

- resamples: 10,000
- seed: `20260818`
- CI: percentile 95%
- primary hypothesis: one-sided improvement claimを行う場合も、two-sided 95% CIを報告

### 30.2 Paired comparison

CandidateとMarketは同一raceで比較する。別sampleの平均を独立比較しない。

### 30.3 Multiple testing

Primary testはALL scopeの1 testに限定する。

- venue別はsecondary heterogeneity analysis
- odds帯別はsecondary
- month別はstability diagnostic
- 良いsegmentだけをPrimaryへ昇格しない

券種間はWIN → WIDE → TRIOの階層を使うか、券種ごとのclaimを完全に分離して明記する。

### 30.4 Minimum practical effect

`FREEZE_REQUIRED`

統計的に微小な改善を成功としないため、minimum practical effect \(\delta_{min}\) をpower analysis前に定める。

決め方:

1. Market loss改善とcandidate probability ratioの関係をsimulationする。
2. odds drift / payoutを含む経済評価へ変換する。
3. 必要なturnoverとvarianceを考慮する。
4. 実務上価値のある最小値を券種別に固定する。

---

## 31. Power analysis

### 31.1 手順

1. development期間からrace-date blockごとのpaired loss差を作る。
2. 想定Edge \(\delta\) を注入する。
3. calendar-contiguous blockをresampleする。
4. 80%以上のpowerを得るrace数・期間を推定する。
5. WIN / WIDE / TRIOで個別に必要sampleを算出する。

### 31.2 Holdout終了条件

race数だけで停止するとcalendar biasが残るため、calendar durationとrace数の両方を条件にする。

推奨default:

```text
holdout_end = max(
  12 calendar months,
  3,000 eligible races,
  power-analysis-required sample
)
```

途中結果に応じて延長・短縮してはならない。延長ルールは開始前に固定する。

---

## 32. CORE評価

### 32.1 位置づけ

COREはPrimary probability testではなく、事前登録されたbusiness-scope secondary endpointとする。

比較可能性のため、V1の帯を継続候補とする。

| 券種 | CORE候補 |
|---|---:|
| WIN | 8 <= snapshot odds < 25 |
| WIDE | 10 <= snapshot lower odds < 20 |
| TRIO | 30 <= snapshot odds < 80 |

`FREEZE_REQUIRED`: 継続するか、変更するかをfuture holdout前に決定する。V1結果を見て都合の良い帯へ変更しない。

### 32.2 Market期待と収益性の分離

normalized marketに対するhit / expected ratioが1を超えても、必ずしも収益性を意味しない。

WINでは、normalized market \(q_i\) とodds \(o_i\) の関係から、概ね次が成り立つ。

\[
p_i o_i = \frac{p_i/q_i}{Z}
\]

したがって、Market比1.0超だけではbreak-even条件を満たさない。確率改善と実払戻ROIを別々に報告する。

---

## 33. Bet-selection protocol

Probability modelとbet-selectionを分離する。

### 33.1 選定順序

1. Probability Edgeを確認
2. calibrationを確認
3. conservative execution oddsを構築
4. candidate selection ruleをdevelopmentで固定
5. prospective paper-tradingで評価

### 33.2 Conservative odds haircut

snapshot oddsからfinal payoutまでの変化を考慮する。

例:

\[
odds_{effective} = odds_{snapshot} \times h_\tau
\]

\(h_\tau\) はdevelopment期間のfinal / snapshot ratioの保守的分位点から固定する。

- Primary decision timeごとに一つのhaircut
- venue別最適化はPrimaryでは行わない
- final holdoutで再推定しない

### 33.3 Expected value

\[
EV = p_{candidate} \times odds_{effective} - 1
\]

selection thresholdはdevelopmentで固定する。

### 33.4 Primary staking

`RECOMMENDED`:

- flat stake
- 1 ticket = 1 unit
- compoundingなし
- Kelly stakingはsecondary simulationのみ
- race後の追加入力・手動選別禁止

### 33.5 主要経済指標

- number of candidate bets
- turnover
- hit count
- gross return
- net return
- ROI
- expected ROI at decision time
- realized / expected ratio
- max drawdown
- longest losing streak
- venue / month stability
- odds slippage distribution

---

## 34. Robustness analysis

Primary判定後に以下を実施する。

- 4 venue
- field size
- distance band
- surface / going
- odds band
- month / meeting
- favourite rank
- class
- snapshot staleness
- model uncertainty

Robustness analysisは「好成績segmentを発見してPrimary失敗を救済する」ために使用しない。

---

## 35. Negative controls and audits

必須監査:

- label permutationでEdgeが消える。
- runner order shuffleでset model出力が不変。
- future rowを混ぜるleakage testが検出される。
- same-day later raceを参照しない。
- snapshot after decision timeを参照しない。
- scratch前後でcandidate universeが正しく変化する。
- missing oddsを未来値でforward-fillしない。
- Market-only modelが再現可能。
- manual lossとframework lossが一致する。
- pair / combo probabilityの総和が1。
- WIN / WIDE / TRIO derived probability sanity check。

---

# Part VIII. Execution Phases and Gates

## 36. Phase 2A: Governance and Data Readiness

### 目的

ProjectをV1から独立させ、時点付きデータと監査基盤を確立する。

### 作業

- V1 dossierをimmutable referenceとして保存
- Phase 2 repository作成
- Project Charter承認
- odds snapshot collector実装
- timestamp / clock sync監査
- raw manifest作成
- race / runner / ticket key設計
- exclusion rule作成
- data-quality dashboard作成

### Deliverables

- `PHASE2_PROJECT_PLAN.md`
- `PHASE2_DATA_CONTRACT.md`
- `PHASE2_MARKET_SNAPSHOT_CONTRACT.md`
- `PHASE2_EXCLUSION_CONTRACT.md`
- `PHASE2_DATA_READINESS_REPORT.md`

### Exit Gate

- Primary decision time候補でcoverage基準を満たす。
- time semanticsが監査可能。
- leakage-prone joinが分離されている。
- raw data hash manifestがある。

不合格ならモデル検証へ進まず、`PHASE2_DATA_NOT_READY`とする。

---

## 37. Phase 2B: Market Baseline

### 目的

最善のMarket-only benchmarkを確立する。

### 作業

- Raw Market再現
- WIDE lower / upper候補比較
- power calibration
- field-size sensitivity
- Market calibration report
- execution odds drift report

### Deliverables

- `MARKET_BASELINE_CONTRACT_WIN.md`
- `MARKET_BASELINE_CONTRACT_WIDE.md`
- `MARKET_BASELINE_CONTRACT_TRIO.md`
- `MARKET_CALIBRATION_REPORT.md`

### Exit Gate

- 券種ごとにPrimary Calibrated Marketが1つに固定。
- gamma等のparameterがdevelopmentのみで決定。
- final holdoutでMarket calibrationを再推定しない契約が完成。

---

## 38. Phase 2C: Legacy Residual

### 目的

V1の119 featuresがMarket residual用途で価値を持つかを切り分ける。

### 作業

- V1 feature semantic再現
- feature parity audit
- Market-offset objective実装
- 事前固定6 configs以内でwalk-forward
- calibration / paired loss評価

### Exit Gate

- H1のdevelopment判定を記録。
- H1が失敗しても結果を削除しない。
- H2へ進む場合、H1とは別仮説として記録。

---

## 39. Phase 2D: New Racing Features

### 目的

speed、pace、class、course biasの追加価値を測る。

### 実施順

1. Speed
2. Pace
3. Class
4. Same-day bias
5. Current pre-race information
6. Approved full set

### Exit Gate

- 各feature blockのstrict as-of audit完了。
- Full candidateをdevelopment foldsだけで1つに選択。
- Primary WIN candidateをfreeze。

---

## 40. Phase 2E: WIDE Joint Model

### 目的

WIDEのpair依存をtop-3 joint modelで改善できるか検証する。

### 作業

- ticket-level market residual baseline
- simple Plackett-Luce
- derived WIDE probability
- joint outputをticket residualへ投入
- lower-upper market情報の統合

### Exit Gate

- probability normalization audit通過
- runner shuffle invariance通過
- direct residualよりdevelopmentで改善
- Primary WIDE candidateを1つにfreeze

---

## 41. Phase 2F: TRIO Joint Model

### 目的

WIDEで検証済みのjoint frameworkをTRIOへ拡張する。

### 作業

- exact top-3 probability
- ticket-level Market offset
- computational efficiency audit
- direct combo baseline比較

### Exit Gate

- exact combination probability audit通過
- memory / runtimeが実運用可能
- Primary TRIO candidateを1つにfreeze

---

## 42. Phase 2G: Preregistration and Freeze

future holdoutを開始する前に、以下を完了する。

- hypotheses freeze
- data cutoff freeze
- model code freeze
- config freeze
- feature list freeze
- decision time freeze
- sample size / end rule freeze
- candidate selection rule freeze
- CORE rule freeze
- statistical test freeze
- prediction output schema freeze

### Freeze artifacts

```text
docs/PHASE2_PREREGISTRATION.md
configs/primary_win.yaml
configs/primary_wide.yaml
configs/primary_trio.yaml
manifests/model_artifact_manifest.json
manifests/feature_manifest.json
manifests/holdout_lock.json
git tag: phase2-preregistered-v1
```

---

## 43. Phase 2H: Prospective Shadow Evaluation

### 運用

- race前にpredictionを生成
- timestamp付きで保存
- hash固定
- race後にlabelとsettled payoutをjoin
- developerには集計途中結果を開示しない
- data-quality alertだけを開示

### 許容される修正

- collector停止等の純粋なインフラ障害
- labelに依存しないparser bug
- security issue

### 許容されない修正

- 成績不振を理由とするparameter変更
- feature追加
- candidate threshold変更
- venue exclusion
- odds帯変更
- decision time変更

修正が予測値に影響する場合、原則として新version・新holdoutが必要。

---

## 44. Phase 2I: Final Evaluation

評価順:

1. data integrity
2. exclusion count
3. Primary Market baseline
4. Primary Candidate loss
5. paired bootstrap CI
6. calibration
7. CORE
8. economic metrics
9. robustness
10. final status

結果を見てPrimary判定ロジックを変更しない。

---

## 45. Phase 2X: External Information

Keibabook等は、Phase 2本体がMarket + NARを評価した後、独立して行う。

比較:

\[
Market + NAR
\quad vs \quad
Market + NAR + External
\]

必要事項:

- data license / usage permission
- published time
- missingness
- source revision history
- subjective label encoding rule
- separate search budget
- separate future holdoutまたはhierarchical test

Externalを用いてPhase 2本体の失敗を事後救済しない。

---

# Part IX. Governance and Reproducibility

## 46. 推奨repository構成

```text
nankan-phase2/
  README.md
  docs/
    PHASE2_PROJECT_PLAN.md
    PHASE2_PREREGISTRATION.md
    PHASE2_DATA_CONTRACT.md
    PHASE2_FEATURE_CONTRACT.md
    PHASE2_MODEL_CONTRACT_WIN.md
    PHASE2_MODEL_CONTRACT_WIDE.md
    PHASE2_MODEL_CONTRACT_TRIO.md
    PHASE2_EVALUATION_CONTRACT.md
    PHASE2_AMENDMENT_LOG.md
  configs/
    data/
    features/
    models/
    evaluation/
  data/
    raw/
    manifests/
    staging/
    curated/
    feature_store/
  src/
    ingestion/
    validation/
    features/
    market/
    models/
    evaluation/
    audit/
  tests/
    unit/
    integration/
    leakage/
  predictions/
    development/
    prospective/
  audit/
    data/
    features/
    models/
    holdout/
  reports/
    development/
    confirmation/
  environment/
    lockfile
    container/
```

---

## 47. Run manifest

各実験で以下を保存する。

```yaml
run_id: null
created_at: null
git_commit: null
data_manifest_hash: null
feature_manifest_hash: null
model_config_hash: null
train_period: null
valid_period: null
holdout_accessed: false
random_seed: null
library_versions: {}
metrics: {}
artifacts: []
notes: null
```

---

## 48. 役割分離

可能なら以下を分ける。

| Role | 責務 |
|---|---|
| Research Lead | 仮説、モデル、development選択 |
| Data Lead | snapshot、時刻、source integrity |
| Audit Reviewer | leakage、contract、search budget監査 |
| Holdout Custodian | prediction lock、結果開示 |
| Operations Owner | collector、daily run、alert |

一人Projectの場合でも、スクリプト・権限・暗号学的hashで役割分離を擬似的に実装する。

---

## 49. Review points

future holdout開始前に、第三者または独立レビューで以下を確認する。

- V1とPhase 2の境界が明確か。
- V1結果を利用した変更がdevelopment扱いになっているか。
- actual snapshotの時点が安全か。
- Market-only baselineが十分強いか。
- feature contractにavailability timeがあるか。
- search budgetが固定されているか。
- Primary Candidateが一つか。
- holdout終了条件が結果非依存か。
- economic evaluationがfinal payoutと整合するか。

---

# Part X. Risk Register

## 50. 主要リスクと対策

| Risk | 影響 | 対策 |
|---|---|---|
| snapshot時点が不正確 | 購入不能情報を使用 | clock sync、published/captured time分離 |
| final-like oddsを使用 | 実運用Edgeを過大評価 | actual snapshot必須 |
| odds drift | EV過大評価 | conservative haircut、realized payout評価 |
| same-day leakage | course bias過大評価 | official time基準、unit test |
| class code変更 | feature不整合 | versioned mapping |
|大量探索 | false positive | search budget、Primary 1 candidate |
| venue救済 | cherry-picking | ALL primary、venue secondary |
| 低sample | inconclusive | power analysis、calendar + race count rule |
| scratch処理不備 | 確率・候補不整合 | universe再構築、audit |
| WIDE lower-only bias | benchmark不適切 | lower/upper候補を少数事前登録 |
| joint model過複雑 | 過学習 | simple PLから段階化 |
| identity overfit | 市場既知情報の暗記 | smoothing、uncertainty |
| external source leakage | 公表時刻不明 | source timestamp contract |
| operational outage | 欠損選択バイアス | outage ruleを事前固定 |
| final holdout閲覧 | 再利用不能 | custodian、hash、access log |

---

# Part XI. Deliverables

## 51. 必須文書

- [ ] `PHASE2_PROJECT_PLAN.md`
- [ ] `PHASE2_PREREGISTRATION.md`
- [ ] `PHASE2_DATA_CONTRACT.md`
- [ ] `PHASE2_MARKET_SNAPSHOT_CONTRACT.md`
- [ ] `PHASE2_FEATURE_CONTRACT.md`
- [ ] `PHASE2_MODEL_CONTRACT_WIN.md`
- [ ] `PHASE2_MODEL_CONTRACT_WIDE.md`
- [ ] `PHASE2_MODEL_CONTRACT_TRIO.md`
- [ ] `PHASE2_EVALUATION_CONTRACT.md`
- [ ] `PHASE2_SEARCH_BUDGET.md`
- [ ] `PHASE2_HOLDOUT_LOCK.md`
- [ ] `PHASE2_AMENDMENT_LOG.md`
- [ ] `PHASE2_FINAL_DOSSIER.md`

## 52. 必須監査artifact

- [ ] raw source hash manifest
- [ ] snapshot coverage report
- [ ] timestamp drift report
- [ ] race / runner / ticket join audit
- [ ] strict as-of feature audit
- [ ] current-race prohibited-column audit
- [ ] feature parity audit
- [ ] probability normalization audit
- [ ] framework loss vs manual loss audit
- [ ] search-budget consumption log
- [ ] model artifact hash
- [ ] prospective prediction submission log
- [ ] holdout access log

---

# Part XII. Decision Rules

## 53. Stage gate一覧

| Gate | PASS条件 | FAIL時 |
|---|---|---|
| G0 Data | snapshot時点・coverage・join品質 | Data整備、confirm開始禁止 |
| G1 Market | calibrated baseline固定 | baseline設計見直し、未holdout |
| G2 Legacy Residual | developmentでMarket改善候補 | H1記録後、H2へ |
| G3 Racing Features | full candidateを1つfreeze | feature仮説REJECT |
| G4 WIDE Joint | direct residualより改善 | direct residualのみ維持 |
| G5 TRIO Joint | exact combo改善 | TRIO REJECT可 |
| G6 Preregistration | 全contract・hash完了 | holdout開始禁止 |
| G7 Probability Confirm | paired CI上限<0 | `PHASE2_REJECT` |
| G8 Economic Confirm | pre-registered ROI基準 | economic inconclusive |
| G9 Operations | run成功・欠損管理 | live pilot禁止 |

---

## 54. Go / No-Go原則

### GO

- actual pre-race snapshotsを安定取得できる。
- Calibrated Marketをbaselineにできる。
- Market-offset objectiveを実装できる。
- speed / pace / class / biasをstrict as-ofで作れる。
- untouched future holdoutを確保できる。
- model-search budgetを守れる。

### CONDITIONAL GO

- WIDE / TRIO joint modelは、WINおよびticket residual基盤が安定した後。
- sequence / embeddingは、表形式・単純モデルを上回る明確な理由がある場合。
- external sourceは、独立したPhase 2Xとして実施。

### NO-GO

- actual snapshotなしで収益性を主張する。
- V1期間をPhase 2 confirmとして再利用する。
- 同じ119 featuresでalgorithmだけを大量探索する。
- holdout結果後にvenue、帯、thresholdを変更する。
- 一部segmentだけの改善でALLの失敗を救済する。
- search budgetを結果に応じて追加する。

---

# Part XIII. Immediate Start Plan

## 55. 最初に実施する順序

### Step 1: ProjectをV1から分離

- 新repositoryまたは新top-level directoryを作る。
- V1 dossierをread-onlyで参照する。
- Phase 2のProject ID、version、ownerを固定する。

### Step 2: Market snapshotを最優先で開始

- snapshot collectorを作る。
- capture timeをJST/UTCで保存する。
- T-30からlast snapshotまで可能な範囲を収集する。
- 結果ラベルを見ずにcoverageとstalenessを監査する。

### Step 3: Data contractを作る

- race key
- runner key
- ticket key
- timestamp semantics
- scratch / cancel / dead-heat rule
- WIDE lower-upper semantics
- payout join rule

### Step 4: V1 baselineを再現

- 119 featuresのsemantic parityを確認する。
- V1のMarket / Model結果を可能な範囲で再現する。
- Phase 2 codebaseの回帰testにする。

### Step 5: Calibrated Marketを作る

- Raw Market
- power calibration
- WIDE baseline候補
- execution odds drift

### Step 6: Legacy Market-offsetを実装

- Market-onlyへ戻るunit test
- manual loss一致
- residual shrinkage
- 6 configs以内

### Step 7: 新featureをブロック単位で作る

- Speed
- Pace
- Class
- Course bias
- Current pre-race
- Market trajectory

### Step 8: WIN Primary Candidateをfreeze

WINでMarket residualの基本設計を確立してから、WIDE / TRIOへ進む。

### Step 9: WIDE / TRIO joint model

- simple PL
- derived ticket probabilities
- ticket Market offset
- direct modelとの比較

### Step 10: Preregistrationとfuture holdout

全contract、code、config、hash、終了条件を固定してからProspective Confirmationを開始する。

---

# Part XIV. Preregistration Checklist

## 56. Holdout開始前チェックリスト

### Project boundary

- [ ] V1は`NAR_ONLY_PROJECT_GIVEUP`のまま変更していない。
- [ ] Phase 2は別Project IDを持つ。
- [ ] V1確認済み期間をfuture holdoutから除外した。

### Hypotheses

- [ ] H0-H5のうち実施する仮説を固定した。
- [ ] Primary hypothesisを券種ごとに一つ定義した。
- [ ] exploratory hypothesesを明示した。

### Data

- [ ] Primary decision timeを固定した。
- [ ] snapshot coverage基準を固定した。
- [ ] timestamp semanticsを固定した。
- [ ] same-day ruleを固定した。
- [ ] scratch / cancel / dead-heat ruleを固定した。
- [ ] holdout start / end ruleを固定した。

### Features

- [ ] feature listをhash化した。
- [ ] availability timeを監査した。
- [ ] future aggregationがない。
- [ ] feature block budgetを固定した。

### Models

- [ ] Calibrated Marketを一つに固定した。
- [ ] Primary Candidateを一つに固定した。
- [ ] model configをhash化した。
- [ ] search budgetを使い切ったか否かにかかわらず追加探索しない。

### Evaluation

- [ ] Primary metricを固定した。
- [ ] bootstrap unit / seed / resamplesを固定した。
- [ ] minimum practical effectを固定した。
- [ ] CORE帯を固定した。
- [ ] bet selection ruleを固定した。
- [ ] staking ruleを固定した。
- [ ] economic success criterionを固定した。

### Operations

- [ ] prospective prediction outputをrace前にlockできる。
- [ ] prediction hashを保存できる。
- [ ] outcome labelの閲覧権限を分離した。
- [ ] outage ruleを固定した。
- [ ] amendment processを用意した。

---

# Part XV. Recommended Primary Design

## 57. 推奨する最小実行構成

開発資源を集中する場合、最初のPrimary構成は以下とする。

### WIN

```text
Baseline:
  Calibrated Market at primary decision time

Candidate:
  Market-offset GBDT
  + V1 legacy features
  + strict as-of speed
  + pace-set features
  + class completeness
  + same-day bias
  + current pre-race available information

Primary metric:
  paired race log loss difference

Primary scope:
  ALL venues

Secondary:
  V1 CORE band, venue, calibration, ROI
```

### WIDE

```text
Baseline:
  Calibrated WIDE Market using frozen lower/upper rule

Candidate:
  Ticket-level Market-offset
  + joint top-3 probability feature
  + pair interaction
  + market trajectory

Primary metric:
  paired WIDE pair cross entropy difference
```

### TRIO

```text
Baseline:
  Calibrated TRIO Market

Candidate:
  Ticket-level Market-offset
  + exact top-3 joint probability
  + combo interaction

Primary metric:
  paired exact-combination log loss difference
```

---

## 58. 開発上の優先順位

| 優先度 | 項目 | 判断 |
|---:|---|---|
| 1 | actual odds snapshots | Phase 2 confirmの前提 |
| 2 | Calibrated Market | 全モデルの基準 |
| 3 | Market-offset objective | Phase 2の中心 |
| 4 | speed / pace / class / bias | 最重要の新情報群 |
| 5 | WIN confirm | 最初の確率Edge検証 |
| 6 | WIDE joint model | 構造改善余地が大きい |
| 7 | TRIO joint model | 最後に実施 |
| 8 | market trajectory | snapshot蓄積後に追加 |
| 9 | identity / sequence | 前段通過後 |
| 10 | Keibabook | 独立Phase 2X |

---

# Part XVI. Final Policy

## 59. Phase 2の最終方針

Phase 2は、次の考え方で進める。

1. **Marketを外部benchmarkではなく、モデルのbaseline / offsetとして内部に置く。**
2. **モデルはレースをゼロから予測せず、Marketの残差だけを学習する。**
3. **actual pre-race odds snapshotを最重要データとする。**
4. **V1で未検証だったspeed、pace、class、course biasをstrict as-ofで追加する。**
5. **WINで設計を確立し、WIDE、TRIOへ段階的に進む。**
6. **WIDE / TRIOはticket residualとjoint top-3 modelを組み合わせる。**
7. **PrimaryはALL scopeのpaired Market comparisonとし、venueや帯で救済しない。**
8. **probability edgeとeconomic edgeを分けて判定する。**
9. **future holdoutを結果から隔離し、predictionをrace前にlockする。**
10. **新しい仮説を追加する場合は、Protocol Amendmentと新holdoutを要求する。**

### 最も重要な判断

Phase 2の成否を左右するのは、CatBoostか別アルゴリズムかではない。

- どの時点のMarketを使ったか
- Marketをどうcalibrateしたか
- Market残差を直接objectiveにしたか
- speed / pace / class / biasがstrict as-ofか
- final holdoutが本当に未使用か
- search budgetを守ったか

である。

この条件を満たせない場合、複雑なモデルを追加しても、再現可能な中荒れEdgeを証明する研究にはならない。

---

# Appendix A. Primary Experiment Registry Template

```yaml
experiment_id: P2-WIN-R001
ticket_type: WIN
hypothesis: H2
status: PLANNED

market:
  decision_time: null
  normalization: inverse_odds_race_normalized
  calibration: power_gamma
  gamma_source: development_only

features:
  legacy_v1: true
  blocks:
    - P2_SPD
    - P2_PACE
    - P2_CLASS
    - P2_BIAS
  manifest_hash: null

model:
  family: market_offset_gbdt
  objective: race_log_loss
  config_id: null
  config_hash: null
  random_seed: null

selection:
  folds: null
  primary_metric: paired_ll_vs_calibrated_market
  search_budget_used: null

confirmation:
  holdout_start: null
  holdout_end_rule: null
  primary_scope: ALL
  bootstrap_block: race_date
  bootstrap_resamples: 10000
  bootstrap_seed: 20260818
  minimum_practical_effect: null

betting:
  core_band: null
  edge_threshold: null
  odds_haircut: null
  staking: flat_one_unit

approvals:
  research_lead: null
  data_lead: null
  audit_reviewer: null
  frozen_at: null
```

---

# Appendix B. Holdout Lock Template

```json
{
  "project_id": "NANKAN_MARKET_RESIDUAL_PHASE2",
  "lock_version": "1.0",
  "locked_at": null,
  "git_commit": null,
  "data_cutoff": null,
  "holdout_start": null,
  "holdout_end_rule": null,
  "primary_decision_time": null,
  "eligible_race_rule_hash": null,
  "feature_manifest_hash": null,
  "model_artifact_hashes": {
    "WIN": null,
    "WIDE": null,
    "TRIO": null
  },
  "evaluation_contract_hash": null,
  "result_visibility": "CUSTODIAN_ONLY_UNTIL_END",
  "amendment_policy": "NEW_VERSION_AND_NEW_HOLDOUT_IF_PREDICTIONS_CHANGE"
}
```

---

# Appendix C. Final Dossier Outline

```text
1. Executive Summary
2. Preregistered Hypotheses
3. Deviations and Amendments
4. Data Coverage and Timestamp Audit
5. Market Baseline
6. WIN Results
7. WIDE Results
8. TRIO Results
9. Calibration
10. CORE and Economic Evaluation
11. Robustness
12. Leakage and Reproducibility Audit
13. Search Budget Consumption
14. Final Status
15. Scope of Conclusion
16. Next Research Conditions
```

---

**Document status:** `DRAFT_FOR_PREREGISTRATION`  
**次の必須作業:** Data Contract、Market Snapshot Contract、Preregistrationの作成とfreeze。
