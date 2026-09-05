# GIT003 Ignore Scope Fix Report

- STATUS: GIT003_PASS
- starting HEAD: `433b499abcfdbe37bddfd31e62bf7a9e9b783e83`
- ending HEAD: `SELF`
- `.gitignore` changed rules: `/db/`, `/raw/`, `/downloads/`, `/artifacts/`, `/models/`, `/outputs/`, `/runs/`, `/logs/`, `/tmp/`, `/temp/`, `/audit/`, `/handoff/`, `/handoff_*/`, `/*_HANDOFF_BUNDLE/`
- recovered source/config paths: `src/audit/` (102 files), `src/models/` (16 files), `configs/models/` (7 files); total 125 files
- recovered source inventory (`src`, `tests`, `scripts`, `tools` scope): `docs/evidence/git_bootstrap/GIT003_RECOVERED_SOURCE_CANDIDATES.txt`
- required Job004 implementation: `src/audit/p2s_job004_frozen_long_run.py`
- src/audit tracked count after commit: 102
- forbidden root paths tracked: 0
- restricted payload files: 0
- real secret matches: 0
- worktree clean after commit: YES
- remote main SHA after push: `SELF`
