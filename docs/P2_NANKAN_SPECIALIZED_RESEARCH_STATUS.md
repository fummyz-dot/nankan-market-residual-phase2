# P2 Nankan-specialized research status

`P2_NANKAN_SPECIALIZED_ONE_LAST_FEASIBILITY_TEST` is a separately scoped research continuation.

- 031 remains the immutable and valid closeout of the OLD Phase2 Actual middle-odds thesis.
- Expert review introduced a NEW bounded Nankan-specialized information-structure hypothesis.
- Phase2 is not silently reopened; Actual betting remains disabled.
- No production, model, policy, threshold, or venue-selection change is authorized by 032.

WIN: `BLOCKED_BEFORE_MODEL`  
WIDE: `CURRENT_HYPOTHESIS_CLOSED`  
TRIO: `BLOCKED_BEFORE_MODEL`

Pre-model gates: K1_HORSE_IDENTIFIABILITY=PARTIAL, K2_JOCKEY_IDENTIFIABILITY=PASS, K3_COURSE_PACE_SUPPORT=FAIL, K4_DYNAMIC_STATE_SUPPORT=PARTIAL, K5_CONDITION_SIMILARITY_SUPPORT=PARTIAL, K6_SAME_DAY_RECONSTRUCTIBILITY=FAIL, K7_WIN_MARKET_BASELINE=PARTIAL, K8_TRIO_MARKET_BASELINE=FAIL, K9_WIN_TARGET_SAMPLE_SUPPORT=PASS, K10_TRIO_TARGET_SAMPLE_SUPPORT=PARTIAL.

Authority: [P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032](../audit/reports/P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032.md).

## 033 prospective data-collection continuation

This section is additive; it does not alter the immutable 031 closeout or the
032 historical-information conclusion.

- OLD_ACTUAL_THESIS: `CLOSED`
- NANKAN_SPECIALIZED_RESEARCH: `COLLECT_SPECIFIC_DATA_FIRST`
- ACTUAL_BETTING: `DISABLED`
- WIDE: `CURRENT_HYPOTHESIS_CLOSED`
- TRIO: `MODEL_BLOCKED_DATA_COLLECTION_ONLY`
- WIN: `DATA_COLLECTION_BEFORE_SINGLE_M1`

`P2_NANKAN_SPECIALIZED_COLLECTION_CONTRACT_V1` freezes only prospective raw
measurement. Contract SHA-256 is
`1abe874932ee1ad373faccc0b83ac35828d3765c1f493d38846fc8e2554a8718` and the
effective cohort start is `2026-09-04`; no day before the frozen contract may
be promoted. The collection cap is 240 COMPLETE race-days or 12 calendar
months. No Actual betting, M1 implementation, model training, policy, or
threshold is authorized by 033.

## 034 operational-entrypoint correction

`P2_NANKAN_SPECIALIZED_PROSPECTIVE_DATA_CONTRACT_033` remains
`SCIENTIFIC_CONTRACT_VALID`; its reported `./specialized-collect` operator
entrypoint had an `OPERATIONAL_ENTRYPOINT_DEFECT_FOUND`.  The former no-argument
invocation only reached argparse because 033 implemented maintenance ledger
commands, not a live collection runner.  034 adds the foreground no-argument
live collection entrypoint without changing 031, 032, the 033 scientific
contract, any predictive model, or Actual betting state.

## 036 live-runtime hardening

This is additive operational hardening only; it does not rewrite 031, 033,
034, or 035. Normal collection-only operation is `./specialized-collect`.
The runtime uses a kernel-owned single-writer lock, append-only event ledger,
immutable per-race T15 artifacts, automatic resume, and an isolated P4 spool.

- ACTUAL_BETTING: `DISABLED`
- WIN: `DATA_COLLECTION_BEFORE_SINGLE_M1`
- WIDE: `CURRENT_HYPOTHESIS_CLOSED`
- TRIO: `MODEL_BLOCKED_DATA_COLLECTION_ONLY`
- REAL_DAY_PRELIVE_CHECK: `PENDING`
