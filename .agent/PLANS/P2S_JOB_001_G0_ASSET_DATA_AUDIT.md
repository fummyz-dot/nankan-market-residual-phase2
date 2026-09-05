# Codex Job Plan

## Job metadata
- Job ID: P2S_JOB_001
- Title: G0 Asset / Data Audit
- Status: COMPLETE
- Owner: Codex

## Objective
Produce a read-only evidence inventory for the Phase 2 successor Feature Contract design.

## Allowed inputs
- `/home/nabe/projects/nankan-market-residual-phase2/`
- `/home/nabe/projects/nkDb-pro/`
- Existing SQLite databases, source archives, manifests, reports, tests, runtime code, and artifacts under those paths.

## Read-only inputs
- Entire predecessor repository.
- `reference/v1/` and all existing Phase 2 assets/databases.

## Allowed modifications
- `.agent/PLANS/P2S_JOB_001_G0_ASSET_DATA_AUDIT.md`
- `audit/g0/G0_20260904_210952/` only.

## Forbidden actions
- DB writes, VACUUM, REINDEX, migration, collector/specialized-collect launch, model training, network access, Keibabook retrieval, betting, source/reference mutation, or scientific-policy changes.

## Tasks
1. Record repository and SQLite identity/integrity with read-only connections.
2. Inventory timing, feature columns, joins, source corpus, models/features, collector static evidence, and artifacts 027–036.
3. Examine post-cutoff evidence without declaring unproven safety.
4. Generate required CSV/JSON/Markdown artifacts, manifest, issues, and deterministic validation output.

## Required artifacts
- Every artifact listed in the P2S_JOB_001 request, in the single timestamped G0 run directory.

## Tests / acceptance criteria
- Every required artifact exists and parses as its declared format.
- SQLite access uses `mode=ro`, with no mutating SQL or external calls.
- All `quick_check` results are recorded; non-`ok` is a blocker.
- Final manifest self-check records all prohibited-action flags as `false`.

## Leakage and temporal checks
- Do not infer timing availability, feature eligibility, or post-cutoff safety where local evidence is insufficient.
- Record unavailable/ambiguous evidence as `UNCLASSIFIED`, `UNKNOWN`, or `REQUIRES_FURTHER_AUDIT`.

## Process supervision
- One foreground, synchronous, bounded audit process; no child workers.

## Run manifest requirements
- `vcs_mode: none`; `git_commit: null`; workspace and timestamps; source/code/config/output SHA-256 records; platform/library versions; `random_seed: null`; commands and output artifacts.

## Completion report
Summarize written paths, executed validation, audit status, unresolved evidence, and reference immutability.

## Completion
- Run directory: `audit/g0/G0_20260904_210952/`.
- Validation: all required CSV/JSON artifacts parsed; required artifact presence passed; manifest prohibited-action flags all `false`.
- Final status: `G0_BLOCKED` because the explicitly requested predecessor path was absent. No substitute repository was used.
- No source DB, V1 reference asset, collector, model, or external resource was modified/accessed.
