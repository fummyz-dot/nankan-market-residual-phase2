# Codex Job Plan

## Job metadata
- Job ID: P2S_G0_A001_PREDECESSOR_REPO_DECOMMISSION
- Title: G0 predecessor repository decommission amendment
- Status: COMPLETE
- Owner: Codex

## Objective
Apply the owner-authorized predecessor decommission classification to a new immutable G0 amendment run, while preserving every other G0 finding.

## Allowed inputs
- Amendment text supplied in this task.
- `audit/g0/G0_20260904_210952/` parent audit artifacts.
- Existing reference DB files under `reference/v1/db/` opened read-only.

## Read-only inputs
- Entire primary repository except the amendment plan and new amendment-run directory.
- All reference/V1 assets and parent G0 artifacts.

## Allowed modifications
- `.agent/PLANS/P2S_G0_A001_PREDECESSOR_REPO_DECOMMISSION.md`
- `audit/g0/G0_20260904_212524/` only.

## Forbidden actions
- Predecessor restoration, alternate-repository discovery, Git-history reconstruction, DB mutation, collector launch, model training, network access, or changes to non-predecessor G0 findings.

## Tasks
1. Reconfirm current reference DB SHA-256, required table counts, and `PRAGMA quick_check` with read-only SQLite connections.
2. Record predecessor as `OWNER_DECOMMISSIONED`, Git history as `NOT_AVAILABLE`, and reference DB identity as confirmed only when all specified evidence matches.
3. Carry forward unchanged G0 findings by exact parent-artifact hash references; generate amended identity, DB, issue, manifest, and final-report artifacts.
4. Validate artifact parsing and prohibited-operation flags.

## Required artifacts
- New amendment manifest, final report, repository identity, DB identity evidence, issues, parent-artifact provenance, and validation record.

## Tests / acceptance criteria
- Both current reference DB SHA-256 and all specified counts match this amendment's supplied values.
- Both `quick_check` results equal `ok`.
- No `BLOCKER` remains solely because the predecessor repository is absent.
- Final status is `G0_PASS_WITH_WARNINGS` only if all above pass and parent non-predecessor findings remain unchanged.

## Leakage and temporal checks
- Preserve the parent `UNKNOWN` / `READ_ONLY_DIAGNOSTIC` post-cutoff finding without converting it to safe or blocker.

## Process supervision
- One foreground, synchronous process; no child workers.

## Run manifest requirements
- `vcs_mode: none`, `git_commit: null`, SHA-256 provenance, commands, artifacts, platform, and non-stochastic seed.

## Completion
- Amendment run: `audit/g0/G0_20260904_212524/`.
- Current reference DB hashes, specified row counts, and `PRAGMA quick_check` all passed.
- The only status-affecting reclassification is predecessor absence: `OWNER_DECOMMISSIONED` rather than a blocker.
- Parent non-predecessor artifacts were carried forward by SHA-256-referenced copy.
- No predecessor restoration, DB mutation, collection, training, network access, or betting was performed.
