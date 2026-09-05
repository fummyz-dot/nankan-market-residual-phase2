# MODEL_EVALUATION_FREEZE_V1 — Amendment 001
## Starter / Tie / DNF Target Semantics

This amendment resolves only the pre-training target-integrity gap discovered by Job004.
It does **not** change features, folds, M1–M4, PL, bootstrap, S2/R1/R2, or WIDE floor gates.

### Existing audited result semantics

Reuse the repository's already-audited `starter_status` semantics. Do not create a new vocabulary.

- `STARTER_VALID_FINISH`: actual starter with valid official numeric finish.
- `STARTER_NO_VALID_FINISH`: actual starter with no valid numeric finish (e.g. race stopped / DNF).
- resolved cancellation/exclusion/nonstarter: not a starter.
- `UNRESOLVED_OUTCOME_STATUS`: hard block.

Historical source code already treats tied finish displays as the same official rank, DNF as a starter without valid numeric finish, and cancellation/exclusion as nonstarters.

## 1. Historical modeling universe

Job003 canonical feature rows remain immutable: **246,709** rows.

Job004 model/PL universe is actual starters only.

Expected from the existing preflight:

- races: **21,560**
- actual starter rows: **244,160**

The 2,549 retained nonstarter rows are not deleted. They are simply not model targets and are not members of the historical PL universe.

Any discrepancy from 244,160 after applying the existing audited classifier => BLOCK and report.

## 2. Effective rank

Let `n` be the number of actual starters.

### Valid finishers and official ties

For each official finish rank `r`, let tie-group size be `t`.

Official competition ranking must be valid:

- first distinct rank = 1
- if a group at rank `r` has size `t`, the next distinct rank must be `r+t`

For every horse in that tie group:

`r_eff = r + (t-1)/2`

Examples:

- no tie: rank 3 -> 3.0
- two-way tie at official rank 2 -> both 2.5
- three-way tie at official rank 4 -> all 5.0

No horse-number or time tie-break is permitted.

### Starter with no valid finish

Let:

- `m` = number of valid finishers
- `q = n-m` = number of starters with no valid finish

All such runners form one bottom-censored group:

`r_eff = m + (q+1)/2 = (m+1+n)/2`

This asserts only that they are below valid finishers. It does not invent an order within the DNF group.

### Invariant

For each race:

`sum(r_eff) = n(n+1)/2`

within `1e-12`.

## 3. Runner target

Replace `finish_rank` in the existing formula with `r_eff`:

`z = Phi^-1((n-r_eff+0.5)/(n+1))`

Clip to `[-2.5, 2.5]`.

Runner weight remains `1/n`.

This is a deterministic partial-order encoding, not result imputation.

## 4. Top3 integrity

Every accepted race must still have exactly three distinct official Top3 horses.

All three must be:

- actual starters
- valid finishers

If the official Top3 is ambiguous, BLOCK. Do not resolve it using horse number, time, odds, popularity, payout, or arbitrary ordering.

## 5. Job003 starter-semantics audit before fit

Because the canonical feature table retains nonstarter rows, perform an audit before training.

Recompute these support counts using **actual historical starters only** and `source_race_date < target_race_date`:

- prior_starts
- starts_last_30d / 90d / 365d
- same_venue_starts
- same_distance_starts
- same_venue_distance_starts
- same_surface_starts
- same_direction_starts
- jockey_90d_starts / jockey_365d_starts
- trainer_90d_starts / trainer_365d_starts
- near_distance_200m_starts
- same_venue_near_distance_200m_starts
- same_direction_distance_starts

Also recompute every frozen `P1_RACE_COMPOSITION` aggregate using actual-starter rows only.

Compare with Job003 canonical values at tolerance `1e-12`.

Any mismatch => `JOB004_BLOCKED_JOB003_STARTER_SEMANTICS`.
Do not train and do not silently patch the data.

## 6. Runtime preflight

Before installation, inspect:

`/home/nabe/projects/nankan-market-residual-phase2/.venv-p2-model/bin/python`

for:

- catboost
- scipy
- numpy
- pandas

If all are present, freeze the exact Python/package versions and environment details in `RUNTIME_FREEZE_V1.json` and use that interpreter.

If CatBoost or SciPy is absent:

`JOB004A_RUNTIME_BLOCKED`

Do not install from network and do not substitute another implementation without Research Lead authorization.

For future training set:

```text
OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
```

## 7. Scope

Job004A performs **no model fit**.

It may only:

1. install this amendment authority,
2. regenerate target-integrity audit under effective-rank semantics,
3. audit Job003 starter-count/composition semantics,
4. audit/freeze an already-installed runtime.

M1–M4, all evaluation gates, folds, feature sets and thresholds remain unchanged.

