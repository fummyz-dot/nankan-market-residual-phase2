# P2-A01 Historical Semantic & Class Foundation Audit

## Technical summary

- `conditions_raw` was profiled for all 21,849 South Kanto races in the locked raw-corpus window (2020-01-01 to 2026-07-31). The output is a non-ordinal `P2_CLASS_RULE` draft; it does not calculate class strength or class delta.
- The history DB contains 128 rows after the raw-corpus cutoff. They were excluded from aggregate profiles as an unresolved provenance boundary.
- NAR corner strings are a parse candidate (21,668 races have horse-number tokens that are subsets of DB runner numbers), but grouping/tie semantics are not normalized. Runner first-3F is not recoverable from confirmed NAR fields.
- Of 18,963 South Kanto horse names, 9,294 have at least one other-flat-venue raw history; the name-linked additional-history count is 165,525. This is completeness evidence only, not P2_XVENUE modeling approval.

## Class and ruleset evidence

- `CLASS_RAW_PROFILE.csv` contains 314 year × venue × normalized-condition cells. `CLASS_CANONICAL_MAPPING_DRAFT.csv` contains 144 observed text decompositions, all marked `DRAFT_NON_ORDINAL_REVIEW_REQUIRED`.
- `CLASS_SYSTEM_VERSION_AUDIT.csv` contains 263 venue/token signatures with first/last dates and year-gap flags. A signature change is observed representation only; raw data cannot establish regulatory causality.
- Prize, age, sex, race-type, and grade-token relations are descriptive profiles, not empirical class-strength estimates.

## Pace and external-data gates

- Lap arrays are variable-length strings. A later parser must establish section distance/time semantics and strict-as-of historical use before any feature is considered.
- Keibabook fields are separated into `NAR_REPRODUCIBLE`, `EXT_OBJECTIVE`, `EXT_SUBJECTIVE_TRAINING`, `PROHIBITED_MARKET`, and `UNKNOWN`; prohibited values are not promoted to a primary input.
- All 5 Keibabook target races have one DB match, but they are after the raw-corpus cutoff and are QA-only. Past-performance matching is not attempted because the sample display date has no year.

## Contracts and next step

- `RuleOnly` and `Rule+Empirical` are the only registered future class ablation candidates. The empirical candidate is design-only and requires strict-as-of, cold-start, uncertainty, and holdout protocols.
- Historical same-day bias is `PRIMARY_PROHIBITED`; publication/capture time is not established.
- P2-A02 can proceed independently on prospective input/capture contracts. A pace parser or P2_XVENUE feature job requires a new approved protocol.
