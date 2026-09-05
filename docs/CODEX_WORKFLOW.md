# Codex Workflow

## Before every non-trivial Codex job
1. Create `.agent/PLANS/<job_id>_<short_name>.md` using `.agent/CODEX_JOB_TEMPLATE.md`.
2. State exactly which files/directories may be modified.
3. State exactly which reference inputs are read-only.
4. Define required outputs and acceptance tests.
5. State whether outcome labels/market data may be read.
6. State the research semantics that must not change.

## Preferred job size
One coherent auditable objective per job. Avoid bundling data semantics, model search, and confirmatory evaluation into one task.

## Codex must report
- files changed;
- commands/tests run;
- artifacts produced;
- warnings/unknown semantics;
- whether any research assumption was inferred;
- whether any frozen/reference asset was touched.

## Gitless provenance (current workspace rule)
This WSL-local workspace does not currently use Git as an operational prerequisite. Do not require a Git commit, initialize a repository, or infer version provenance from Git state.

Every run manifest must include:
- `vcs_mode: none`;
- `git_commit: null`;
- `workspace_root` and `created_at`;
- `code_manifest_sha256`, `input_manifest_sha256`, and `config_manifest_sha256`;
- `python_version`, `platform`, `library_versions`, and `random_seed` (`null` when non-stochastic);
- `artifacts` and `commands`.

SHA-256 records must cover input files, active code, configuration, outputs, source/destination paths, and generation time. Reference manifests are separate from active-code manifests.

## Failure behavior
If a semantic ambiguity could affect leakage or the scientific claim, stop the job and emit an audit artifact instead of guessing.

## Long-running and parallel execution
Use foreground, synchronous, bounded, checkpointed execution by default. If a job truly requires background or parallel workers, follow `docs/PROCESS_SUPERVISION_POLICY.md`: persist worker state and paths, atomically heartbeat and checkpoint, require fresh heartbeat plus progress for `RUNNING`, collect every child exit code, fail the parent on any child failure/staleness, write `COMPLETE` only after all success, and audit for orphan processes at job closeout.
