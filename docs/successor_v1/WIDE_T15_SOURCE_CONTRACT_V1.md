# WIDE T15 Source Contract V1

**Project:** `NANKAN_PHASE2_SUCCESSOR_RL_V1`  
**Stage:** Stage 2 source preflight only  
**Status:** FROZEN before Stage 2 market-baseline selection  
**GitHub authority start commit:** `a11f507b8b14d1d812052188f93689c1b6db03c5`

## 1. Scope

This contract freezes only the source and eligibility semantics for a prospective
South-Kanto WIDE `T15_STANDARD` market observation.

It does **not** choose an interval-to-point odds conversion, market probability
mapping, calibration parameter, incremental-edge metric, or betting rule.

## 2. Canonical prospective store

```text
/home/nabe/projects/nankan-market-residual-phase2/db/market_snapshot.sqlite
```

Schema authority:

```text
/home/nabe/projects/nankan-market-residual-phase2/src/ingestion/prospective_store.py
```

Required tables:

```text
race_registry
source_captures
current_info_snapshots
current_runner_info
market_snapshots
```

## 3. T15_STANDARD

Decision time is:

```text
scheduled_post_time - 15 minutes
```

Primary WIDE capture time must be inside:

```text
[decision_time - 60 seconds, decision_time]
```

inclusive.

The matching CURRENT T15 row must have:

```text
snapshot_mark = T15
target_decision_label = T-15_ENGINEERING_CANDIDATE
t15_timing_status = PREDECISION_VALID
capture_status = COMPLETE
availability_evidence in {
  PUBLISHED_AT_CONFIRMED,
  OBSERVED_IN_PREDECISION_RAW_CAPTURE
}
notes.market_wide_status = COMPLETE
notes.market_wide_capture_id != null
notes.market_capture_set_rule =
  EXACT_T_MARK_OFFICIAL_WIN_WIDE_AND_TRIO_NOT_LATEST
```

The WIDE capture must be the explicit official WIDE link reached from the exact
same T-mark WIN page. A later/latest odds page must never substitute for it.

## 4. WIDE pair universe

The active roster is the `current_runner_info` set attached to the eligible T15
CURRENT snapshot.

For `n` active runners:

```text
expected pair count = C(n, 2)
```

Every unordered pair must appear exactly once. Missing, duplicate, and extra
pairs are prohibited.

## 5. WIDE displayed odds interval

Canonical stored numeric fields are:

```text
lower = market_snapshots.odds_value
upper = market_snapshots.max_odds_value
```

with:

```text
0 < lower <= upper
finite(lower)
finite(upper)
```

These are an official displayed **interval**. Job005 must not turn it into an
exact price.

In particular, Job005 must not choose among prior development candidates such
as lower-only, geometric mean, or arithmetic/harmonic-style conversion.

## 6. Primary exclusions

The following are not Stage-2 primary market observations:

```text
T20
T10
T05
RECOVERY
PRE_RACE_FALLBACK
late T15
stale T15
partial WIDE pair universe
historical MARKET_TIME_UNKNOWN odds
```

T10/T05 are future relative to T15 and cannot backfill T15.

## 7. Historical market reference

Read-only path:

```text
/home/nabe/projects/nankan-market-residual-phase2/reference/v1/db/nankan_market.sqlite
```

Expected SHA-256:

```text
62450b078badcf2fc675416a068c83548a620ae5aa02d22bd91d8fedca0001ad
```

Historical official odds are:

```text
MARKET_TIME_UNKNOWN_DEVELOPMENT_REFERENCE_ONLY
```

and are not Stage-2 primary evidence.

`odds_snapshots` is expected to contain zero rows.

Job005 may inspect schema/row counts only. It must not read payouts or outcomes.

## 8. Job005 is outcome-blind

Job005 is prohibited from:

- reading race results or payouts;
- constructing winning-pair labels;
- computing market q/probabilities;
- choosing an interval point conversion;
- fitting gamma/beta;
- computing CE/logloss/Brier/ROI/profit;
- evaluating Job004 model performance against market;
- performing performance bootstrap.

## 9. What comes after Job005

After the source preflight, Research Lead separately freezes:

1. interval-to-point market mapping;
2. market calibration protocol;
3. incremental-information test and gate;
4. prospective sample/support rule.

Economic edge remains Stage 3.
