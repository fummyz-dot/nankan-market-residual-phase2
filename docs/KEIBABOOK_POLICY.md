# Keibabook External Data Policy

## Status
Collect from the start; do not mix into the main NAR+Market primary candidate unless an approved protocol explicitly does so.

Live Ability/Training context is `CONTEXT_ONLY`. A missing target race,
unavailable source, or context-parser review condition is recorded in the
analysis bundle as unavailable context and must not block valid FS04/Main
analysis or a recommendation. It never authorizes fabricated context values.

## Ability-table JSON
Allowed objective candidates include:
- runner-level historical first-3F;
- last-3F;
- pace category;
- runner corner positions;
- race grade/condition information;
- objective horse/jockey/trainer/record fields.

Fields that must remain excluded from model input:
- RT;
- CPU prediction;
- development/pace prediction;
- win odds;
- historical popularity;
- raw text if it contains uncontrolled subjective/market information.

Trial/retraining-trial past events must be tagged separately from official races.

## Training JSON
Potential feature families:
- days since final workout;
- number/frequency of workouts;
- course-specific time cells;
- load/intensity;
- final-workout intensity;
- change vs previous preparation;
- slope/hill indicators;
- paired-work presence;
- paired-work outcome (ahead/behind/same);
- paired horse class if reliably parseable;
- `中間軽め` indicator.

Absolute workout times must not be compared across heterogeneous courses without normalization.

## Phase 2X experiment split
Prefer separate blocks:
- `EXT-A`: ability-table objective history;
- `EXT-B`: training;
- `EXT-C`: EXT-A + EXT-B.
This preserves incremental-value attribution.
