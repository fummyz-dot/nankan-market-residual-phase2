# Git Workflow V1

## Source of truth

GitHub commit SHA identifies the exact code/specification state used for each reproducible job.

Every future Codex job should record:

- repository full name
- branch
- starting commit SHA
- ending commit SHA
- scientific authority files read
- runtime freeze hash
- dataset manifest hashes
- run/attempt ID

## Branch model

Use:

- `main`: accepted/reviewed state
- `codex/<job-id>`: Codex implementation branch
- `fix/<short-name>`: narrow implementation fixes
- `research/<short-name>`: only for pre-result scientific design changes

Do not perform result-driven rescue changes directly on `main`.

## Codex standard procedure

1. Fetch/checkout current `main`.
2. Record starting commit SHA.
3. Create `codex/<job-id>`.
4. Implement only the authorized job.
5. Run tests/audits.
6. Commit all source/spec changes.
7. Report ending commit SHA and changed paths.
8. Open a PR to `main` when practical.
9. Research Lead / ChatGPT reviews diff and evidence.
10. Merge only after acceptance.

## Long-running model jobs

For long runs, commit/freeze the implementation **before model fit**.

The run manifest must contain the commit SHA used for the fit.

Do not edit tracked source files during the fit. If a blocker is discovered:
- stop,
- commit a new amendment/fix on a new branch/commit,
- start a new attempt ID.

## Generated outputs

Large runtime outputs remain local and are not committed.

Only curated summaries may be copied to `docs/evidence/`.

## Review contract

When reporting completion, Codex should include:

```text
GIT:
- repo:
- branch:
- start_commit:
- end_commit:
- dirty_worktree: NO

CHANGED:
- <path>
- <path>

AUTHORITY:
- <path + sha256>

TEST/AUDIT:
- <status>

RUN:
- attempt_id:
- model_fit: YES/NO
```

## Implementation model

Default Codex implementation model for this project: **Sol**.
