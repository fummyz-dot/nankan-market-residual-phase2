# MODEL_EVALUATION_FREEZE_V1 — Amendment 007
## Structural Top3 Surprisal for `n_r = 3`

**Status:** FROZEN BEFORE race-head / final PL / bootstrap  
**Implementation model:** Sol

## Mathematical issue

The frozen structural target is:

```text
U_r = -log(max(P0(A_r), 1e-12)) / log(C(n_r,3))
```

For exactly three actual starters:

```text
C(3,3) = 1
log(1) = 0
```

The only unordered Top3 set is the entire starter set, so under a valid joint Top3 model:

```text
P0(A_r) = 1
-log(1) = 0
```

Therefore the normalized structural surprisal is `0/0`, not a meaningful numeric zero.

## Frozen semantics

For:

```text
n_r >= 4
```

use the existing structural target unchanged.

For:

```text
n_r = 3
```

set:

```text
structural_target_status = STRUCTURAL_TARGET_UNDEFINED_TRIVIAL_FIELD
U_r = NaN / null
```

Do not impute `U_r=0`.

## Race-head

`n_r=3` races:

```text
race-head target training = excluded
R1 = excluded
upset_score = NOT_APPLICABLE / NaN
z_upset = 0
temperature = M0 T0
```

No gamma modulation is applied.

## M1 temperature fitting

Fit upset-score standardization and `T0/gamma` only on structurally eligible:

```text
n_r >= 4
```

races.

Compute `mu` and population `sigma` only from those race-head crossfit scores.

## R1

Use only defined structural-label races:

```text
n_r >= 4
```

for:

- fold Spearman
- pooled Spearman
- race-head MAE
- constant-baseline MAE

The fold-specific constant baseline is the mean `U_r` over available outer-TRAIN crossfitted labels with `n_r>=4`.

## R2

Evaluate M1-vs-M0 only on:

```text
n_r >= 4
```

outer-VALID races.

The date-block bootstrap for R2 uses only races contributing to that R2 statistic.

## Probability model / S2 / WIDE

Do **not** remove the `n_r=3` races from the probability universe.

They remain in:

- B0 / Primary runner modeling
- ordinary joint Top3 probability evaluation
- S2 probability-edge evaluation
- WIDE output and WIDE metrics
- WIDE floor support/calibration

For a valid `n_r=3` race:

```text
unordered Top3-set probability = 1
unordered Top3 NLL = 0
each WIDE pair probability = 1
sum WIDE pair probability = 3
```

For those races, M1 is defined to use the same `T0` as M0, so gamma has no effect.

## Known affected races

```text
20230505_FUNABASHI_05
20230707_KAWASAKI_05
```

## Required audit

Create:

```text
/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/structural_surprisal_domain_audit.csv
```

The audit must prove that `n_r=3` races are excluded only from the structural-head domain, not from the ordinary probability/WIDE universe.

