# Git Bootstrap Instructions
## Run after the currently-running Codex job is no longer writing repository files

Repository root:

`/home/nabe/projects/nankan-market-residual-phase2`

Target GitHub repository name:

`nankan-market-residual-phase2`

Recommended visibility:

`PUBLIC`

## Important timing rule

Do not run `git add`, mass file scans, file moves, cleanup, or commits while the current long Job004 attempt is actively writing files.

The safe bootstrap point is after that attempt stops, passes, fails, or blocks.

## Step 1 — Place bootstrap files

Copy:

`.gitignore.phase2.proposed`

to:

`/home/nabe/projects/nankan-market-residual-phase2/.gitignore`

Only do this if `.gitignore` does not already exist.

If one exists, merge the proposed rules into the existing file instead of overwriting it.

Copy:

`GIT_POLICY_V1.md`

to:

`/home/nabe/projects/nankan-market-residual-phase2/docs/GIT_POLICY_V1.md`

Copy:

`GIT_WORKFLOW_V1.md`

to:

`/home/nabe/projects/nankan-market-residual-phase2/docs/GIT_WORKFLOW_V1.md`

## Step 2 — Safety scan BEFORE git add

From repository root:

```bash
git status --short 2>/dev/null || true

find . -type f \( \
  -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \
  -o -name '*.cbm' -o -name '*.pkl' -o -name '*.pickle' \
  -o -iname '*keibabook*' \
\) -print
```

The files may exist locally; they simply must not become tracked.

## Step 3 — Initialize Git if needed

```bash
cd /home/nabe/projects/nankan-market-residual-phase2

if [ ! -d .git ]; then
  git init -b main
fi
```

Do not delete an existing `.git`.

## Step 4 — Verify ignore boundary

```bash
git status --short --ignored
```

Specifically confirm these are ignored if present:

```text
reference/v1/
db/
data/processed/
outputs/
audit/
artifacts/
.venv-p2-model/
*keibabook*
```

Specifically confirm these are trackable:

```text
src/
tests/
scripts/
tools/
docs/
data/manifests/
```

## Step 5 — Secret/data scan

Before first commit, inspect the staged list:

```bash
git add -n .
```

Do not proceed if staged candidates include:
- database bytes,
- processed datasets,
- raw NAR archives,
- paid Keibabook material,
- credentials/tokens,
- model binaries,
- OOF/output datasets.

## Step 6 — First local commit

Only after the safety review:

```bash
git add .
git status --short
git commit -m "chore: bootstrap Phase2 source repository"
```

Record:

```bash
git rev-parse HEAD
```

## Step 7 — GitHub remote

Create an empty GitHub repository named:

`nankan-market-residual-phase2`

Do NOT initialize it with README, .gitignore, or license if the local initial commit already exists.

Then:

```bash
git remote add origin <GITHUB_REPO_URL>
git push -u origin main
```

If `origin` already exists, inspect it before changing it:

```bash
git remote -v
```

Never replace an existing remote blindly.

## Step 8 — Connect ChatGPT GitHub access

Grant the connected GitHub app access to this repository.

After that, ChatGPT can directly:
- search source code,
- read manifests/specs,
- inspect commits,
- compare diffs,
- review PRs and changed files.

## First post-bootstrap report

Return:

```text
STATUS: GIT_BOOTSTRAP_COMPLETE

REPO:
- GitHub full name:
- visibility:
- local root:
- remote:
- main commit SHA:
- dirty worktree:

SAFETY:
- DB tracked: NO
- processed data tracked: NO
- Keibabook tracked: NO
- model binary tracked: NO
- secrets detected: NO

TRACKED TOP LEVEL:
- ...
```
