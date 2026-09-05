# MODEL_EVALUATION_FREEZE_V1 — Amendment 006
## Exact EB Inner-OOF Replay and Outer-VALID State Update

**Status:** FROZEN BEFORE FIRST Job004 FIT  
**Implementation model:** Sol  
**Scope:** EB time-causal numerical update procedure only.

Algorithm contract SHA-256:

```text
c9aa8c3c7ccec686443f1f78c19dc5a31485461cd0caac3d6cad5d463256140b
```

## 1. Core decision

### Inner-OOF PL score

Use **date-causal EB state**.

Do not use a full-period EB backfit to score its own earlier inner-OOF dates.

### Outer VALID

At the outer-fold boundary:

1. estimate layer variance components from all outer-TRAIN selected-Primary inner-OOF residuals;
2. freeze the four layer-specific `sigma²/tau²` pairs;
3. never re-estimate them during outer VALID;
4. after each VALID date, append that date's residuals and rebuild effects from the entire cumulative residual history using the frozen components.

Thus:

```text
hyperparameters = frozen at outer-fold start
EB effects/state = allowed to evolve using prior dates only
```

## 2. Residual observation

```text
e_i = z_i - fhat_i
```

`fhat_i` is the raw selected Primary GBDT prediction on the target-z scale.

EB group statistics are **unweighted arithmetic observation statistics**.

`n_g` is the integer number of contributing residual observations.

The GBDT `1/n_r` sample weight remains unchanged, but it is not reused as an EB grouping weight.

## 3. One layer update

Layer order:

```text
horse
jockey
horse×venue
jockey×venue
```

For layer `L`:

```text
r_i^(-L) = e_i - sum of all current EB effects except layer L
```

Gauss-Seidel rule:

- layers already updated in the current cycle use the new value;
- later layers use the previous-cycle value.

In REESTIMATE mode:

```text
sigma²_L = mean((r_i^(-L))²)
m_g      = arithmetic mean of r_i^(-L) in group g
n_g      = integer observation count
w_g      = n_g / sum(n_g)
mu       = sum(w_g * m_g)
Var_w    = sum(w_g * (m_g-mu)²)
E_w(1/n) = sum(w_g / n_g)
tau²_L   = max(0, Var_w - sigma²_L * E_w(1/n))
```

Effect:

```text
tau²_L == 0  => effect = 0

otherwise:
effect_g = tau²_L / (tau²_L + sigma²_L/n_g) * m_g
```

## 4. Interaction centering

Immediately after each interaction layer update, before moving to the next layer:

```text
center_parent =
    sum(n_g * raw_interaction_g) / sum(n_g)

interaction_g =
    raw_interaction_g - center_parent
```

Center within horse for horse×venue, and within jockey for jockey×venue.

If the parent has observations in fewer than 2 distinct venues in the state dataset, all its interaction effects are exactly 0.

Main effects are not altered by this centering.

## 5. Contributing rows

Horse:

```text
all residual rows
```

Jockey:

```text
nonmissing jockey rows only
```

Horse×venue:

```text
rows of horses having >=2 distinct venues in the state dataset
```

Jockey×venue:

```text
nonmissing jockey rows of jockeys having >=2 distinct venues in the state dataset
```

Missing jockey always gives:

```text
jockey effect = 0
jockey×venue effect = 0
```

## 6. Backfit reference algorithm

Every reference backfit call starts **all effect maps at zero**.

One cycle:

```text
horse
-> jockey
-> horse×venue + immediate parent centering
-> jockey×venue + immediate parent centering
```

After the full cycle, compute maximum absolute effect change over the union of old/new keys, treating missing keys as zero.

Stop if:

```text
max_abs_change < 1e-5
```

Maximum:

```text
20 cycles
```

If not converged after cycle 20:

- use cycle-20 effects deterministically;
- record `converged=false`;
- record final max change;
- emit a warning;
- do not increase iteration count or alter threshold.

NaN/Inf is a hard block.

## 7. Inner-OOF EB scores used by PL

After the outer-fold Primary candidate is selected, construct strict OOF raw predictions for that candidate.

Process unique inner-OOF `race_date` values ascending.

For date `d`:

```text
D_<d = selected-candidate inner-OOF residual rows with race_date < d
```

Build the EB state by full reference backfit on `D_<d` in **REESTIMATE mode**.

If `D_<d` is empty, every EB effect is zero.

Score every date-`d` runner with:

```text
S_i^OOF =
    fhat_i^OOF
  + horse_effect
  + jockey_effect
  + horse×venue_effect
  + jockey×venue_effect
```

Only after **all races on date d have been scored** may date-d historical outcomes form residuals for dates `> d`.

PL M0/M1 must use these date-causal OOF scores.

A full-period OOF backfit followed by scoring earlier rows is prohibited.

## 8. Outer-TRAIN component freeze

Define:

```text
D_train =
all selected-candidate strict inner-OOF residuals
from 2021 through outer-TRAIN end
```

Run full backfit from zero on `D_train` in REESTIMATE mode.

From the final executed cycle, freeze one pair per layer:

```text
sigma²_horse,         tau²_horse
sigma²_jockey,        tau²_jockey
sigma²_horse×venue,   tau²_horse×venue
sigma²_jockey×venue,  tau²_jockey×venue
```

Then run a **second** full backfit from zero on the same `D_train` in FIXED_COMPONENT mode.

Those effects are the state for the first outer-VALID date.

## 9. Outer-VALID update

The selected Primary GBDT is fitted once on all outer-TRAIN rows and stays fixed.

For date `d`, all races use the state frozen before `d`.

After all date-d races are evaluated:

```text
e_i = z_i - fixed_outer_train_GBDT_raw_prediction_i
```

Do **not** subtract EB effects from this residual.

Append date-d residuals:

```text
D_state_after_d =
D_train
union all outer-VALID residuals with race_date <= d
```

Then run a full reference backfit from zero on `D_state_after_d` in FIXED_COMPONENT mode.

This rebuilt effect state is used only for dates `> d`.

During outer VALID:

```text
sigma²/tau² re-estimation = PROHIBITED
same-day race-to-race update = PROHIBITED
GBDT refit = PROHIBITED
```

## 10. Optimized implementation

Caching or sufficient-statistic acceleration is allowed only if it reproduces the reference cumulative full-rebackfit algorithm on a deterministic fixture.

If equivalence is not demonstrated, implement the reference algorithm.

## 11. Audit

Required output:

```text
/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/eb_state_update_audit.csv
```

Must make it possible to prove:

- inner-OOF score at date `d` used residual dates `< d` only;
- no date-d residual was used for a date-d score;
- outer-VALID `sigma²/tau²` never changed;
- state residual row count grows monotonically;
- GBDT was not refit during outer VALID.

