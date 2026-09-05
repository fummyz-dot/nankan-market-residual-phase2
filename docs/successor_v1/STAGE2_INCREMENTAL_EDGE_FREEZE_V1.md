# Stage 2 Incremental Edge Freeze V1

Status: **FROZEN BEFORE PROSPECTIVE OUTCOME EVALUATION**

## Primary question

Does the successor probability model add predictive information beyond the exact
prospective `T15_STANDARD` WIDE market?

This stage is not an ROI test.

## Successor probability engine

Primary scorer is a continuation of the already validated Job004 Fold4 pipeline.

- Primary CatBoost candidate: `M2`
- CatBoost remains the Fold4 model trained through `2025-12-31`
- No retraining before the initial Stage2 evaluation closes
- Race-head outer Fold4 model remains fixed
- Fold4 M1 `T0`, `gamma`, upset mean/sigma remain fixed
- EB variance components remain fixed
- EB effects are reconstructed date-causally through `2026-07-31`, then continued date-causally
- no same-day result updates
- `n=3` retains Amendment007 M0-T0 semantics

The model pair distribution is:

```text
q_model(i,j) = p_wide(i,j) / 3
```

because exact WIDE marginal mass is 3 per race.

`p_safe` is not used in Stage2.

## Market mapping

For displayed WIDE interval `[L,U]`, the primary price point is the geometric midpoint:

```text
O = sqrt(L * U)
m = -log(O) = -0.5 * (log L + log U)
q_raw = softmax(m)
```

This is a pair-allocation distribution with race mass 1. It is not asserted to
be an exact event probability.

Fixed sensitivity mappings:

```text
LOWER_ENDPOINT: m = -log(L)
UPPER_ENDPOINT: m = -log(U)
```

No mapping may be selected after outcome inspection.

## Outcome target and score

For official Top3 horses `a,b,c`, the realized WIDE pair target has mass `1/3`
on `(a,b)`, `(a,c)`, `(b,c)` and zero elsewhere.

Race pair cross-entropy:

```text
CE_r(q) = -(1/3) * sum_over_three_winning_pairs log(q_pair)
```

## Market calibration

For race date `d`, use only eligible settled races with `race_date < d`.

```text
q_market(gamma) = normalize(q_raw ** gamma)
gamma in [0.25, 4.0]
```

Fit gamma with equal race weight by deterministic bounded scalar optimization
on `log(gamma)`, `xatol=1e-8`.

Warmup requires at least:

```text
20 prior races
4 prior race dates
```

Warmup races are not formal gate-evaluation races.

## Incremental residual tilt

Define:

```text
s = log(q_model) - log(q_market)
q_hybrid(beta) = normalize(q_market * exp(beta*s))
               = normalize(q_market**(1-beta) * q_model**beta)
beta in [0,1]
```

`beta=0` is the exact market-only baseline.

For date `d`, beta is fit only on prior race dates.

Primary race delta:

```text
delta_r = CE_hybrid - CE_market
```

Negative is better.

## Formal support gate

Formal Stage2 PASS/FAIL is not allowed until all are met:

```text
>= 100 gate-evaluation races
>= 12 gate-evaluation race dates
>= 10 gate-evaluation races in each of 大井 / 川崎 / 浦和 / 船橋
```

Before that:

```text
STAGE2_ACCUMULATING
```

not FAIL.

## Statistical gate

Date-block bootstrap:

```text
10,000 resamples
seed = 20260905
percentile 95% CI
>= 9,900 valid resamples
```

PASS requires all:

```text
mean(delta) < 0
bootstrap upper95(delta) < 0
LOWER_ENDPOINT sensitivity mean(delta) <= 0
UPPER_ENDPOINT sensitivity mean(delta) <= 0
```

Venue metrics are diagnostics, not separate significance gates.

## Leakage boundary

For any date `d`:

- model/market prediction artifact must exist before reading `d` outcomes;
- all races on `d` are scored before any `d` outcome updates state;
- only dates `< d` may update EB, gamma, or beta;
- historical `MARKET_TIME_UNKNOWN` odds cannot substitute for T15;
- T10/T05 cannot substitute for T15.

Economic edge, ticket selection, ROI, and profit remain Stage3.
