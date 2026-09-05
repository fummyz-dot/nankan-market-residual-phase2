# MODEL_EVALUATION_FREEZE_V1 — Amendment 005
## History DB Path Correction

**Status:** FROZEN BEFORE FIRST Job004 FIT  
**Change class:** PATH CORRECTION ONLY  
**Implementation model:** Sol

Amendment 004 specified a non-existent Phase2 path:

```text
/home/nabe/projects/nankan-market-residual-phase2/db/nankan_history.sqlite
```

That path is superseded.

The canonical immutable V1 history DB inside the Phase2 repository is:

```text
/home/nabe/projects/nankan-market-residual-phase2/reference/v1/db/nankan_history.sqlite
```

Required SHA-256:

```text
5fe7a9e88e25f64e51e39e27b789315ababfbe597786b26701f0e4a7f8486936
```

Open it read-only.

## Explicitly not authorized

Do not substitute:

```text
/home/nabe/projects/nankan-market-residual-phase2/db/p2_history_context.sqlite
```

Do not use a live-history DB or a schema-compatible DB selected by inference.

## Required preflight

Before any model fit:

1. file exists
2. SHA-256 matches exactly
3. `PRAGMA quick_check` = `ok`
4. tables include `horses`, `imports`, `race_runners`, `races`
5. counts equal 19,086 / 166 / 251,373 / 21,977
6. rerun Amendment 004 EB key checks against this DB
7. Job003B 244,160 model rows join 1:1 by `(race_key, horse_number)`

If absent or hash differs:

```text
JOB004_BLOCKED_HISTORY_DB_AUTHORITY
```

No fallback is authorized. Everything else remains unchanged.

