# WIDE T15 Source Contract V1 — Amendment 001
## Legacy capture-set rule

**Amendment ID:** `WIDE_T15_SOURCE_CONTRACT_V1_AMENDMENT_001_LEGACY_CAPTURE_SET_RULE`  
**Base contract:** `WIDE_T15_SOURCE_CONTRACT_V1`  
**Base JSON SHA-256:** `41267996673ff0a4f7053f2a49f24e41e545469d80a11b519e91f5e480c8ade5`  
**Frozen before:** Stage 2 market-baseline selection / performance evaluation

## Trigger

JOB005 stopped with `JOB005_BLOCKED_DATA_CONTRACT` on exactly eight races:

```text
2026-08-28 船橋04R-11R
```

Their stored CURRENT provenance token was:

```text
EXACT_T_MARK_OFFICIAL_WIN_AND_WIDE_NOT_LATEST
```

while the original Job005 source contract required only the newer token:

```text
EXACT_T_MARK_OFFICIAL_WIN_WIDE_AND_TRIO_NOT_LATEST
```

No outcome, payout, market-vs-model performance, CE/logloss/Brier, bootstrap, ROI,
or model fit was used to make this amendment.

## Scientific decision

The Stage 2 primary market object is **WIDE**.

TRIO capture is additive provenance for a different ticket family. Its presence is
not required to establish that a WIDE observation is the exact T-mark official
predecision WIDE market.

Therefore the following two exact CURRENT provenance tokens are accepted:

```text
EXACT_T_MARK_OFFICIAL_WIN_AND_WIDE_NOT_LATEST
EXACT_T_MARK_OFFICIAL_WIN_WIDE_AND_TRIO_NOT_LATEST
```

The semantic minimum is:

```text
EXACT_T_MARK_OFFICIAL_WIN_AND_WIDE_NOT_LATEST
```

Unknown or weaker capture-set tokens remain a hard provenance violation whenever
the row otherwise claims standard-complete T15 semantics.

## What does NOT change

All WIDE-specific safeguards remain mandatory:

- T15 mark and `PREDECISION_VALID`;
- complete CURRENT capture;
- explicit non-null WIDE capture id and WIDE COMPLETE status;
- official Nankan source;
- `P2_MKT_ONLY` and exact `T15` source mark;
- same-T-mark WIN linkage when both identifiers are present;
- exact predecision time window;
- complete `C(n,2)` active-roster pair universe;
- positive finite WIDE lower/upper interval with `upper >= lower`;
- exact raw/source response hash consistency;
- `PRIMARY_CANDIDATE`;
- `T-15_ENGINEERING_CANDIDATE`;
- `PROSPECTIVE_TIMESTAMPED_STABILIZATION`;
- `quality_status=COMPLETE`.

## Data policy

Do **not** rewrite the eight existing database rows.

Do **not** backfill TRIO.

Do **not** relabel their stored provenance token.

The audit must interpret the two exact known tokens under this amendment while
preserving the original stored evidence.

## Rerun

JOB005 must be rerun outcome-blind from an implementation commit that contains
this amendment and the corresponding narrow audit-code change.

`JOB005_PASS` still requires zero hard contract violations.
