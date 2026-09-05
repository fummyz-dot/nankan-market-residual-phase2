# P2-M12B-R13 Normalized Live-History Compiler

## STATUS

`R13_A_M01_PRIMITIVES_PASS`

R13 is checkpointed. This report and
`audit/data/p2_m12b_r13/R13_PROGRESS.json` are created before compiler work so
an interrupted run has an explicit resume point.

## Boundaries

R4 official raw/provenance is reused. No historical collection, model
retraining, performance evaluation, payout, ROI, or result-ledger inference
access is in scope.

## Checkpoint A — M01-compatible primitives

- Retained official card/result/detail provenance only; network fetches: `0`.
- Source / normalized: `204` races, `2,130` runners, `2,089` canonical horses.
- Base-master matches: `1,984`; genuine post-cutoff canonical horses: `105`.
- Required race and horse primitives unresolved: `0`; canonical collisions: `0`.
- `quick_check=ok`, `foreign_key_check=0`; independent rebuild logical hashes match exactly.
- Next checkpoint: `R13_B_DERIVED_INPUTS`.

## Checkpoint B — derived inputs

R13-B recovered the card-display vocabulary through an exact, audited source
alias allowlist.  Raw tokens are preserved and M02 itself is unchanged.  All
204 class rows, 204 speed race / 2,130 speed runner observations, and 204 pace
race / 2,130 pace runner observations were generated with `quick_check=ok` and
no foreign-key violations.  Next checkpoint: `R13_C_PROVIDER`.

## Checkpoints C/D — shared provider and July replay

- One `P2NormalizedHistoricalAsOfProvider` supplies strict-as-of history to
  V1, Class, Speed, and Pace. Their default historical paths remain unchanged.
- The provider accepts the production base/delta defaults and the isolated July
  simulation cutoff. It excludes target-date rows in both modes.
- July M01 simulation slice: 1,256 races, 12,812 runners, 8,225 horses, with
  original M01 schema/foreign keys intact.
- The earliest M04 replay difference was caused by OTHER_FLAT rows entering
  the simulation M04 delta. The frozen NANKAN input universe is now enforced;
  all 3,330 July M04 observations match the frozen reference exactly.
- Four July FS04 fixtures (one per venue; 44 runner rows) passed exact parity:
  178 features, 0 mismatches, max numeric difference
  `5.000444502911705e-13`. Delta effects were observed for V1, Class, Speed,
  and Pace.

## Live history freshness gate

The R13 date-boundary and July-parity gate is PASS. A later P7 production
preflight found a separate frozen-V1 person-category compatibility issue; that
incident is preserved and is now recovered by exact official pre-race person-ID
crosswalks. It does not change the R13-D parity result.

- Official and normalized deltas each contain 204 races / 2,130 runners through
  2026-08-20; both have `quick_check=ok` and zero foreign-key violations.
- For 2026-08-20, 192 prior delta races are visible, no same-date row is
  visible, and maximum history date is 2026-08-19.
- For 2026-08-21, all 204 delta races are visible and 2026-08-20 is available
  as prior history.
- Result/reconciliation DB access during these provider and parity checks was
  zero.

## P7 production preflight incident and recovery

`BLOCKED_ON_LIVE_HISTORY_V1_CATEGORICAL_TEXT_SEMANTICS`

The base M01 stream and official August delta do not use the same raw text
semantics for frozen V1 `jockey` and `trainer` categories. For example, base
contains `町田直` while the delta contains `町田直希`; delta also contains
unapproved forms such as `[J]原優介`, `[兵]杉浦健太`, and `▲小野俊斗`.
There are 79 distinct delta jockey values and 118 distinct delta trainer
values absent from the base exact vocabularies. Automatic shortening, marker
removal, fuzzy matching, or a category/model change was not used. The former
block is preserved at `audit/data/p2_m12b/P7_LIVE_INFERENCE_PRECHECK_BLOCKED.json`.

P7-R1 now resolves all 2,130 August runner contexts with official person IDs
and exact frozen V1 tokens; unresolved contexts are zero. The model's existing
unseen-category code remains unchanged. Next checkpoint: P7 live-inference
command precheck.
