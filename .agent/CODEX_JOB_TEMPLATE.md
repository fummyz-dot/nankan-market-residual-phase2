# Codex Job Plan

## Job metadata
- Job ID:
- Title:
- Status: PLANNED
- Owner: Codex

## Objective
One sentence describing the auditable outcome.

## Why this job exists
Research/engineering reason. Do not justify by observed holdout performance.

## Allowed inputs
List exact paths.

## Read-only inputs
List exact paths, especially `reference/v1/`.

## Allowed modifications
List exact Phase 2 paths.

## Forbidden actions
- no writes to V1 reference/original repo;
- no unregistered model search;
- no final-holdout access unless the job explicitly is final evaluation;
- no silent semantic inference for leakage-sensitive fields.

## Tasks
1.
2.
3.

## Required artifacts
- 

## Tests / acceptance criteria
- 

## Leakage and temporal checks
- 

## Process supervision (when applicable)
- Prefer foreground, synchronous, bounded, checkpointed work.
- If background/parallel workers are required, identify the supervisor, heartbeat/progress freshness thresholds, checkpoint boundary, stdout/stderr paths, and orphan-process closeout audit.
- `COMPLETE` may be emitted only after every child exit code is collected and successful; any child failure or stale worker fails the parent.

## Run manifest requirements
- `vcs_mode: none`
- `git_commit: null`
- workspace root and creation timestamp
- SHA-256 code, input, and config manifests
- Python version, platform, and library versions
- seed if stochastic (`null` if non-stochastic)
- commands and artifact list with output SHA-256 values

## Completion report
Summarize changed files, commands, outputs, unresolved issues, and whether any assumption changed.
