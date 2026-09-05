# Workspace Policy

## Local operation
This Phase 2 project operates entirely on the WSL local filesystem. Git is not currently used as an operational requirement. Do not initialize Git or require a Git commit for a job.

## Provenance
SHA-256 manifests are the provenance mechanism. A run records source and destination paths, generated timestamp, input/active-code/config/output hashes, a run manifest, environment versions, commands, and artifacts. Gitless manifests use `vcs_mode: none` and `git_commit: null`.

## V1 isolation
`/home/nabe/projects/nkDb-pro` is a read-only V1 source. `reference/v1/` is an independent immutable copy. Phase 2 active files must not be placed under `reference/v1/`, and reference inputs must not be mixed into active Phase 2 namespaces.
