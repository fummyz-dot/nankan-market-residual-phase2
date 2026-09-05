# Git / Public Repository Policy V1

## Purpose

GitHub is the source of truth for **code, scientific authority, schemas, tests, and reviewable configuration** for `nankan-market-residual-phase2`.

Local filesystem remains the source of truth for large/private/restricted data and runtime outputs.

## Repository visibility

Public is permitted for the code/specification repository, provided the exclusion rules below are enforced.

## Track in Git

- `src/`
- `tests/`
- `scripts/`
- `tools/` when they contain reproducible source code
- `docs/`
- `docs/jobs/`
- `docs/evidence/` for manually curated small evidence
- `data/manifests/`
- schema-only SQL / CSV definitions
- environment constraints/lock files that contain no credentials
- `.gitignore`
- `README.md` / project governance files

## Do NOT track

- SQLite databases (`*.sqlite`, `*.db`, WAL/SHM)
- `reference/v1/` copied data/repository snapshot
- raw NAR archives
- `data/processed/`
- materialized training datasets
- OOF predictions / bulk outputs
- model binaries (`*.cbm`, pickle/joblib/ONNX/etc.)
- virtual environments / wheelhouses
- unrestricted `audit/` trees
- secrets, tokens, credentials
- paid/restricted Keibabook data, including training/nouryoku JSON exports

## Audit evidence policy

`audit/` is ignored by default because it may contain large or row-level evidence.

When an audit artifact is useful for scientific review, create a **small curated copy** under:

`docs/evidence/<job_id>/`

Before committing, confirm it contains no:
- restricted-source payload,
- credentials,
- raw personal information,
- large row-level corpora.

Prefer hashes, counts, formulas, summaries, and compact validation tables over raw data.

## Database lineage in Git

Database bytes are not committed. Track only:

- canonical logical path
- expected local path
- SHA-256
- `PRAGMA quick_check`
- table names/counts
- schema-only SQL
- provenance/build metadata

## Scientific authority rule

Machine-readable authority lives under:

`data/manifests/successor_v1/`

Human-readable authority lives under:

`docs/successor_v1/`

A scientific amendment must be committed **before** any result-producing run that depends on it.

## No post-hoc edits

Once a model run has started against a committed authority commit, do not amend that authority on the same run after seeing performance.

A changed scientific specification requires:
1. a new amendment file,
2. a new commit,
3. a new run/attempt ID.
