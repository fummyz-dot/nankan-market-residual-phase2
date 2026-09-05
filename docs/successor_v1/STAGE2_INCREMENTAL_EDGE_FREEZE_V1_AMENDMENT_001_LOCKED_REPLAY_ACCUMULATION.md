# Stage 2 Incremental Edge Freeze V1 — Amendment 001
## Locked Replay and Accumulation

Status: **FROZEN BEFORE REAL STAGE2 OUTCOME REPLAY**

### 1. Scientific classification

The first post-cutoff run is a `DEVELOPMENT_LOCKED_REPLAY`, not a confirmatory
live experiment. The T15 market captures were genuinely prospective, but the
successor Fold4 forward scorer is being reconstructed after those race dates.

The model lineage, market mapping, calibration form, and statistical gates were
frozen before Stage2 outcome evaluation. They may not be changed in response to
the replay.

### 2. Warmup

At the beginning of race date `d`, calibration may use only eligible settled
race dates strictly before `d`.

Warmup requires:

```text
>= 20 prior eligible races
>= 4 prior eligible race dates
```

Before warmup:

```text
gamma = 1.0
beta = 0.0
gate_eligible = false
```

All races on the same date use the same pre-date gamma/beta state.

### 3. Mapping-specific calibration

The primary mapping and both frozen sensitivity mappings are calibrated
separately:

```text
LOG_MIDPOINT_GEOMETRIC
LOWER_ENDPOINT
UPPER_ENDPOINT
```

For each mapping, fit its own `gamma`, then fit its own `beta` using that
mapping's current-date gamma on the same strictly-prior cohort.

After warmup, optimizer failure is a hard date block. No fallback parameter is
allowed.

### 4. Blind until formal support

JOB007 must not unblind real Stage2 performance.

Until the frozen support gate is satisfied and Research Lead explicitly issues
a later unblinding job, tracked evidence and console output may contain only:

```text
support counts
race-date counts
venue counts
pre-outcome exclusion counts/reasons
prediction/reconciliation artifact counts
integrity/leakage status
hashes
```

It must not report:

```text
mean delta
bootstrap CI
sensitivity performance
venue performance
CE/logloss/Brier aggregates
ROI/profit
```

Local untracked state may retain frozen prediction distributions, official
winning-pair labels after reconciliation, EB residual observations, and
calibration parameters needed for later dates.

### 5. Cohort

Market cohort:

```text
race_date >= 2026-08-01
JOB005A classification = T15_STANDARD_ELIGIBLE
```

A race enters the prediction cohort only if its successor prediction artifact
is frozen before that date's outcome reconciliation.

A pre-outcome feature/input failure is `MODEL_INPUT_BLOCKED`; it is excluded
deterministically and counted by reason. It must never be excluded after seeing
the result.

An evaluation row additionally requires an official target with exactly three
distinct Top3 actual starters. Otherwise it is
`OUTCOME_TARGET_UNAVAILABLE` and is counted separately.

### 6. Outcome target

Reuse Job004 starter semantics.

The target is the three unordered WIDE pairs formed by exactly three distinct
official Top3 actual starters.

If a horse withdraws after the frozen T15 snapshot, do not renormalize the
frozen T15 market/model pair distribution. Both market and model are judged
from the same T15 information set.

If a final winning pair is not present in the frozen T15 pair universe,
reconciliation hard-blocks.

### 7. EB continuation

The EB state at the start of date `d` uses only residual observations from race
dates `< d`.

After a date settles, the future-state update universe is **all South-Kanto
target races**, not only races with T15 WIDE market evidence.

For those post-settlement state updates, reproduce Job004 residual semantics on
the final actual-starter support. Reconstruct fixed-M2 raw predictions using
strict prior history and only pre-race-safe target columns, then attach the
outcome-derived target residual. Outcome fields must never enter the raw
prediction.

No same-day EB update is allowed.

### 8. Historical parity before real replay

Real post-cutoff outcome access is prohibited until both pass:

1. Feature adapter parity on 40 deterministic Fold4 races: 10 evenly spaced
   sorted race keys per venue from 2026-01-01 through 2026-07-31.
2. Fold4 scorer parity on all 2026-01-01 through 2026-07-31 Fold4 validation
   races using frozen Job003B materialized inputs.

Required tolerances:

```text
categorical values: exact
missing mask: exact
numeric features: abs<=1e-12 and rel<=1e-12
raw M2 predictions: abs<=1e-12
WIDE probabilities: abs<=1e-10
probability mass: 1e-10
```

Any failure blocks before real Stage2 outcome access.

### 9. Artifact boundary

Each real prediction is an immutable, content-hashed local artifact containing
no result. A date-level freeze marker is required before any outcome for that
same date can be reconciled.

Reconciliation remains local/untracked while blinded.

Formal Stage2 PASS/FAIL is not performed by JOB007.
Economic edge and betting remain prohibited.
