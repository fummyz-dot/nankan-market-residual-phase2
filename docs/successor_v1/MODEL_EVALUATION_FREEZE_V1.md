# MODEL_EVALUATION_FREEZE_V1

**Project:** NANKAN_PHASE2_SUCCESSOR_RL_V1  
**Status:** FROZEN_BEFORE_JOB004_TRAINING  
**Development cutoff:** 2026-07-31

This Markdown is the human-readable companion to `MODEL_EVALUATION_FREEZE_V1.json`.
The JSON file is the machine-readable authority. If the two differ, **BLOCK** and return to Research Lead.

## 1. Inputs

- B0: 55 features
- B0 ordered hash: `0108ffaf8239a0522e5b5157c0ca388bca359866375f704a0d4b42937569b5f6`
- Primary Job003 deterministic features: 130
- Exclude before training: `class_group_no`
- Primary GBDT feature count: 129
- Primary ordered hash: compute and freeze **before the first fit**
- Numeric missing: NaN, no global imputation
- Categorical missing: `__MISSING__`
- Sort rows before CatBoost by `(race_date, race_key, horse_number)`

## 2. Runner target

`z_ri = Phi^-1((n_r - finish_rank_ri + 0.5)/(n_r + 1))`, clipped to `[-2.5, 2.5]`.

Runner weight = `1/n_r`.

Preflight requirement: every eligible starter must have one valid unique numeric rank `1..n_r`.
Any violation => `JOB004_BLOCKED_TARGET_INTEGRITY`; do not impute.

## 3. Outer / inner temporal folds

2020 is initial history only and has no fake OOF predictions.

### Fold1
- Outer train: 2020-01-01..2022-12-31
- Outer valid: 2023-01-01..2023-12-31
- Inner:
  - train 2020 -> valid 2021
  - train 2020-2021 -> valid 2022

### Fold2
- Outer train: 2020-01-01..2023-12-31
- Outer valid: 2024
- Inner: valid years 2021, 2022, 2023 with expanding prior-year training

### Fold3
- Outer train: 2020-01-01..2024-12-31
- Outer valid: 2025
- Inner: valid years 2021..2024 with expanding prior-year training

### Fold4
- Outer train: 2020-01-01..2025-12-31
- Outer valid: 2026-01-01..2026-07-31
- Inner: valid years 2021..2025 with expanding prior-year training

For all features/state: `source_race_date < target_race_date`. Same-day result use is prohibited.

## 4. B0 CatBoost

```text
task_type=CPU
loss_function=RMSE
iterations=600
depth=5
learning_rate=0.03
l2_leaf_reg=20
random_seed=260904
random_strength=0
bootstrap_type=No
grow_policy=SymmetricTree
boosting_type=Plain
has_time=True
allow_writing_files=False
thread_count=1
use_best_model=False
```

## 5. Primary CatBoost search

Only four configs:

| Config | depth | l2_leaf_reg | iterations | learning_rate |
|---|---:|---:|---:|---:|
| M1 | 6 | 10 | 800 | 0.03 |
| M2 | 7 | 10 | 800 | 0.03 |
| M3 | 6 | 30 | 800 | 0.03 |
| M4 | 7 | 30 | 800 | 0.03 |

All other CatBoost parameters equal B0 common deterministic settings.

Selection: mean unordered Top3-set NLL on inner temporal OOF only.
Outer VALID is never used for hyperparameter selection.
If configs are within absolute NLL `1e-4` of the best, choose in complexity order:
`M3 -> M1 -> M4 -> M2`.

No fifth config.

## 6. Structural surprisal and race head

`U_r = -log(max(P0(actual Top3 set), 1e-12)) / log(C(n_r,3))`.

For 2021 structural labels only, `T0=1.0` is the preregistered bootstrap default because no earlier OOF year exists.
For year `y>=2022`, fit B0 `T0` only from inner-OOF years earlier than `y`.

Race-head cross-fit:
- 2021: no race-head OOF prediction
- y>=2022: train only on structural-label years earlier than y, then predict y
- outer VALID: fit on all available outer-TRAIN structural-label years

Race head fixed config:

```text
CatBoostRegressor
loss_function=Huber:delta=1.0
iterations=400
depth=4
learning_rate=0.03
l2_leaf_reg=20
random_seed=260904
random_strength=0
bootstrap_type=No
has_time=True
thread_count=1
```

## 7. EB

Backfit order:
1. horse
2. jockey
3. horse x venue
4. jockey x venue

Use selected Primary GBDT **inner-OOF residuals only**.

`w_g=n_g/sum n_g`  
`mu=sum w_g*m_g`  
`Var_w=sum w_g*(m_g-mu)^2`  
`E_w(1/n_g)=sum w_g*(1/n_g)`  
`tau^2=max(0, Var_w - sigma^2*E_w(1/n_g))`  
`e_g=tau^2/(tau^2+sigma^2/n_g)*m_g`

Maximum 20 backfit iterations; convergence at maximum absolute effect change `<1e-5`.

Interactions are count-weight centered within parent.
If a parent has observations in fewer than two venues, venue interaction = 0.

All races on date d use the EB state frozen before date d. Update only after all date-d results are known.

## 8. Plackett-Luce

`S_ri = GBDT(x_ri)+b_h+c_j+d_hv+g_jv`.

No pure venue intercept.

M0: `eta=S/T0`, `T0 in [0.25,4.0]`.
Optimize `log(T0)` by bounded scalar minimization of mean unordered Top3-set NLL, `xatol=1e-8`.

M1:
`z_upset=clip((upset_score-train_mean)/train_sd,-3,3)`  
`T_r=T0*exp(gamma*z_upset)`  
`T0 in [0.25,4.0]`, `gamma in [0,0.5]`.

Fit M1 only on inner years with cross-fitted race-head predictions (2022 onward).
Use L-BFGS-B, start at `(log(M0_T0), 0)`, `ftol=1e-12`, `gtol=1e-8`, `maxiter=1000`.
Optimizer failure => BLOCK.

Probability identities must hold within `1e-10`:
- ordered Top3 probabilities sum to 1
- unordered Top3 set probabilities sum to 1
- WIDE pair probabilities sum to 3 per race

## 9. Bootstrap

- Unit: `race_date`
- Resamples: 10,000
- Seed: 20260904
- CI: percentile 95%
- Pool all outer-VALID dates, resample unique date blocks with replacement, drawing the original number of dates.
- For ratio metrics, denominator-zero resample is invalid. Fewer than 9,900 valid resamples => BLOCK that gate.

## 10. Gates

### R1 race-head standalone

PASS iff:
- pooled Spearman rho > 0
- at least 3/4 outer folds rho > 0
- pooled race-head MAE < pooled fold-specific constant baseline MAE

Fold baseline = mean structural U in available cross-fitted outer-TRAIN structural-label races.

If R1 fails, final selected model uses M0 and gamma=0.

### R2 temperature

Eligible only if R1 passes.

`delta = NLL_M1 - NLL_M0`.

PASS iff:
- pooled mean delta < 0
- bootstrap 95% CI upper bound < 0

If R2 fails: select M0, gamma=0.

### S2 Probability Edge

Primary selected = M1 only if pooled R1 and R2 both pass; otherwise M0.

`delta = NLL_primary_selected - NLL_B0`.

PASS iff:
- pooled mean delta < 0
- bootstrap 95% CI upper bound < 0
- pooled WIDE race-normalized logloss(primary selected) <= B0

Failure => `JOB004_PROBABILITY_EDGE_FAIL`.

## 11. WIDE floor

Candidate floors, in selection order:

`0.20 -> 0.15 -> 0.10`

Support, pooled:
- tickets >= 500
- expected hits `sum(p) >= 100`

Support, every outer fold:
- tickets >= 75
- expected hits `sum(p) >= 15`

Actual hits are not used for support qualification.

Calibration:
`O/E=sum(hit)/sum(p)`.

`O/E_LCB95` = 2.5th percentile of date-block bootstrap O/E.

PASS iff:
- pooled O/E_LCB95 >= 0.80
- pooled `abs(mean(hit)-mean(p)) <= 0.03`
- each outer-fold point O/E >= 0.70

Choose the highest floor passing all gates.
If none passes => `SHADOW_ONLY`.
Never lower below 10%.

`c=min(1, pooled O/E_LCB95 at selected floor)`  
`p_safe=c*p_wide`.

Live eligibility later requires `p_safe >= selected_floor`.
If no floor is selected, p_safe is not authorized for BUY eligibility.

## 12. Market separation

Job004 may not use:
- official_odds
- runner_market
- payouts
- MARKET_TIME_UNKNOWN
- ROI
- EV
- bet selection

Thurstone is not implemented in Job004.
