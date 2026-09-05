# MODEL_EVALUATION_FREEZE_V1 — Amendment 004
## Exact EB Residual and Grouping-Key Authority

**Status:** FROZEN BEFORE FIRST Job004 FIT  
**Scope:** EB residual source and horse/jockey/venue grouping keys only.

No model fit has been run.

## 1. Residual

For runner `i` in race `r`:

```text
e_ri = z_ri - fhat_ri
```

where:

- `z_ri` is the already-frozen effective-rank target.
- `fhat_ri` is the **raw selected Primary CatBoostRegressor prediction on the z scale**.
- `fhat_ri` is taken **before EB, PL, T0, gamma, race-head, or any probability transformation**.
- Do not center residuals by race.
- Do not subtract a race mean.
- Do not clip the residual again.

The Primary GBDT target/sample-weight contract remains `1/n_r` exactly as already frozen.
This amendment does **not** change or reinterpret the existing EB aggregation/shrinkage weighting.

### Outer-TRAIN residual source

After M1–M4 selection for an outer fold, use that **single selected candidate** for EB residual construction.

For each frozen inner-OOF target year `y`:

```text
fit selected candidate on years < y
predict y
e = z - raw prediction
```

2020 has no OOF residual.

In-sample residuals are prohibited.

### Outer-VALID sequential EB update

After candidate selection:

1. fit one selected Primary GBDT on all outer-TRAIN data;
2. keep that GBDT fixed through outer VALID;
3. for date `d`, apply EB state frozen before `d`;
4. after **all** date-`d` races are evaluated, compute  
   `e = z - fixed_outer_train_GBDT_prediction`;
5. update EB state only for dates `> d`.

No same-day earlier-race update.

## 2. Horse key

Source:

```text
/home/nabe/projects/nankan-market-residual-phase2/db/nankan_history.sqlite
table  = race_runners
column = horse_key
```

Exact authority:

```text
horse_key = race_runners.horse_key
```

Use the stored TEXT value exactly.

Do not reconstruct from horse name/birth date.

For every modeling row it must be nonmissing and valid under:

```text
race_runners.horse_key -> horses.horse_key
```

Failure = BLOCK.

## 3. Jockey key

Source:

```text
/home/nabe/projects/nankan-market-residual-phase2/db/nankan_history.sqlite
table  = race_runners
column = jockey
```

Exact authority:

```text
jockey_key = race_runners.jockey
```

There is no separate Job004 jockey-ID construction.

Do **not**:

- concatenate `jockey_affiliation`
- alias-merge names
- Unicode-normalize
- case-fold
- abbreviate
- construct a hash/key from other columns

For SQL NULL or empty/whitespace-only jockey:

```text
jockey main effect = 0
jockey×venue effect = 0
```

Such rows are excluded from jockey group/variance estimation. They are not pooled into a learned missing-jockey group.

## 4. Venue key

Source:

```text
/home/nabe/projects/nankan-market-residual-phase2/db/nankan_history.sqlite
table  = races
column = venue_code
```

Join:

```text
race_runners.race_key = races.race_key
```

Exact authority:

```text
venue_key = races.venue_code
```

Use exact stored TEXT.

Must be nonmissing. Job004 universe must resolve to the four accepted South-Kanto venues.

## 5. Interaction keys

Use tuple keys, not string concatenation:

```text
horse×venue  = (horse_key, venue_key)
jockey×venue = (jockey_key, venue_key)
```

Missing jockey => jockey×venue effect 0.

If a horse/jockey parent has fewer than two distinct venues in the state used for prediction, its interaction effect is exactly 0.

## 6. History DB authority

Absolute path:

```text
/home/nabe/projects/nankan-market-residual-phase2/db/nankan_history.sqlite
```

Required SHA-256:

```text
5fe7a9e88e25f64e51e39e27b789315ababfbe597786b26701f0e4a7f8486936
```

## 7. Key contract hash

Newline-joined key declarations:

```text
horse_key=race_runners.horse_key
jockey_key=race_runners.jockey
venue_key=races.venue_code
```

SHA-256:

```text
0c5889faba2dc6dfcf762af298683b25f8195d8ccfa78d05ebed6bb131af412e
```

## 8. Preflight

Before any model fit create:

```text
/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/eb_residual_key_preflight.json
```

Hard requirements:

- history DB hash PASS
- Job003B v1.1 244,160 rows join 1:1 to `race_runners` by `(race_key, horse_number)`
- horse_key missing = 0
- horse FK failures = 0
- jockey source is exactly `race_runners.jockey`
- venue source is exactly `races.venue_code`
- venue missing = 0
- venue mapping valid for four venues
- `first_seen_date` / `last_seen_date` not read
- market access = 0
- residual unit test exactly equals `z - raw_primary_prediction`

Failure:

```text
JOB004_BLOCKED_EB_RESIDUAL_KEY_INCONSISTENCY
```


## 9. Existing EB shrinkage math is unchanged

This amendment defines only:

1. which prediction enters `e = z - fhat`, and
2. which stored keys define horse/jockey/venue groups.

The already-frozen EB definitions for:

- backfit order
- `m_g`
- `sigma²`
- `tau²`
- `n_g`
- centering
- convergence
- interaction identifiability

remain unchanged. Do not reinterpret them from this amendment.
