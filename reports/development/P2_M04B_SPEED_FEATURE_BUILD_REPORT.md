# P2-M04B — Runner Speed History Feature Build Report

## 1. STATUS
`READY_FOR_P2_M05_PACE_FOUNDATION`

## 2. Frozen amended speed standard
P2-AMEND-001 `P2_SPEED_STANDARD_MAIN_V1` was read unchanged: all-history hierarchical course-only median baseline, lambda 20, going `NONE`, date-block processing, and provisional model-use status.

## 3. Observation layer and parity
The separate post-race observation dataset has 244367 non-null speed figures and matches M04R on all comparable speed fields (0 mismatches).

## 4. Main history eligibility
Only non-exchange Nankan observations enter state: 242883. Exchange observations are excluded from Main history; other-flat is not read.

## 5. Pre-race feature layer
250093 target runner rows retain history depth, last/recent form, trend, dispersion, and exact-course fields. Cold starts retain NULL aggregates.

## 6. Strict-as-of and leakage
Date-block history gives same-day source rows 0 and current-race source rows 0.

## 7. Data quality and status
`abs(speed_z)>5`: 1008; `abs(speed_z)>10`: 374; no clipping. The block remains `PROVISIONAL_DEVELOPMENT_FEATURE`; no historical period already seen may be used as amended confirmatory evidence.

## 8. Next stage
P2-M05 pace foundation may begin; no going, class-adjusted, Market, or P2_XVENUE speed variant is authorized.
