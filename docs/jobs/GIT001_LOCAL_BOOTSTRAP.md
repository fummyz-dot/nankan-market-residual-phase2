# Codex作業指示
## GIT001 — Phase2 Local Git Bootstrap

IMPLEMENTATION MODEL: Sol

### Duration class
`SHORT_TO_MEDIUM`

### Purpose
Job004 PASS後のrepositoryを、public GitHub公開を前提として安全にGit管理へ移行する。
このJobでは**local Git初期化とfirst commitまで**行う。
GitHub remoteの作成・pushはまだ行わない。

---

# 1. Repository root

```text
/home/nabe/projects/nankan-market-residual-phase2
```

このroot以外を変更しない。

---

# 2. 必須bootstrap filesの存在確認

以下が既に配置されていることを確認する。

```text
/home/nabe/projects/nankan-market-residual-phase2/.gitignore
/home/nabe/projects/nankan-market-residual-phase2/docs/GIT_POLICY_V1.md
/home/nabe/projects/nankan-market-residual-phase2/docs/GIT_WORKFLOW_V1.md
/home/nabe/projects/nankan-market-residual-phase2/docs/jobs/GIT_BOOTSTRAP_AFTER_JOB004.md
```

1つでも無ければ:

```text
STATUS: GIT001_BLOCKED_BOOTSTRAP_FILE_MISSING
```

で停止。

既存 `.gitignore` を上書きしない。

---

# 3. Job004の停止状態確認

次を確認する。

- Job004のactive writer/processが存在しない
- `JOB004_PASS` が最終status
- repository内でmodel runが継続書込み中ではない

判定に使う既存artifact:

```text
/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/JOB004_FINAL_REPORT.md
/home/nabe/projects/nankan-market-residual-phase2/audit/successor_v1/job004/LATEST_ATTEMPT_STATUS.json
```

Job004がまだactiveならGit初期化しない。

---

# 4. Git existing-state preflight

以下を記録:

```bash
cd /home/nabe/projects/nankan-market-residual-phase2
git rev-parse --is-inside-work-tree 2>/dev/null || true
git remote -v 2>/dev/null || true
git status --short 2>/dev/null || true
```

既に `.git` がある場合:

- 削除しない
- `git init` を再実行しない
- branch / remote / tracked filesを監査し、
- 不明な既存履歴があればBLOCK

既存Gitが無い場合のみ後続でinit。

---

# 5. Public-safety filesystem scan

localに存在してよいが、Git trackingは禁止する対象:

```text
reference/v1/
db/
data/processed/
data/raw/
data/staging/
data/curated/
data/feature_store/
outputs/
audit/
artifacts/
models/
.venv/
.venv-*
wheelhouse/
```

拡張子/名称:

```text
*.sqlite
*.sqlite3
*.db
*.sqlite-wal
*.sqlite-shm
*.parquet
*.feather
*.arrow
*.csv.gz
*.jsonl.gz
*.cbm
*.pkl
*.pickle
*.joblib
*.onnx
*.pt
*.pth
*keibabook*
*KeibaBook*
*競馬ブック*
.env
.env.*
*.pem
*.key
*.p12
*.pfx
credentials*.json
secrets*.json
token*.json
```

存在自体はFAILではない。

---

# 6. `.gitignore` hard validation

以下を `git check-ignore -v` で検証できる範囲で確認する。

最低限、存在する場合はignoreされていなければならない:

```text
reference/v1/
db/
data/processed/
outputs/
audit/
.venv-p2-model/
```

`keibabook` 名を含むfileもignoreされること。

一方、以下はignoreされてはいけない:

```text
src/
tests/
scripts/
tools/
docs/
data/manifests/
```

`.gitignore` ruleに矛盾があり、public禁止物がtrack候補になる場合は:

```text
STATUS: GIT001_BLOCKED_GITIGNORE_BOUNDARY
```

で停止し、勝手にgit addしない。

---

# 7. Initialize Git

`.git` が無い場合のみ:

```bash
cd /home/nabe/projects/nankan-market-residual-phase2
git init -b main
```

branchが `main` であることを確認。

---

# 8. Git identity

確認:

```bash
git config user.name
git config user.email
```

両方設定済みならそのまま使用。

どちらかが未設定なら値を推測・作成しない。

```text
STATUS: GIT001_BLOCKED_GIT_IDENTITY
```

として停止。

---

# 9. Dry-run staging

実際のstage前に:

```bash
git add -n .
```

候補pathを保存:

```text
/home/nabe/projects/nankan-market-residual-phase2/docs/evidence/git_bootstrap/GIT_ADD_DRY_RUN.txt
```

`docs/evidence/git_bootstrap/` は作成可。

---

# 10. Dry-run hard rejection

`git add -n .` 候補に以下が1件でも入ればBLOCK:

```text
reference/v1/
db/
data/processed/
data/raw/
outputs/
audit/
.venv
wheelhouse
keibabook
```

または次の拡張子:

```text
.sqlite
.sqlite3
.db
.parquet
.csv.gz
.cbm
.pkl
.pickle
.joblib
.onnx
.pt
.pth
```

BLOCK status:

```text
GIT001_BLOCKED_PUBLIC_DATA_BOUNDARY
```

---

# 11. Strong secret scan

stage候補になるtext filesに対し、最低限以下のstrong patternをscan:

```text
-----BEGIN PRIVATE KEY-----
ghp_
github_pat_
AKIA[0-9A-Z]{16}
AIza[0-9A-Za-z_-]+
sk-[A-Za-z0-9_-]{20,}
```

matchがあればcommitせず:

```text
STATUS: GIT001_BLOCKED_SECRET_SCAN
```

matchしたfile pathだけ報告し、secret値本体はconsoleへ出力しない。

---

# 12. File-size audit

stage候補について:

- 1 MiB超: auditに記録
- 10 MiB超: first commitから除外せず、まずBLOCKしてResearch Leadへ返す

status:

```text
GIT001_BLOCKED_LARGE_TRACKED_FILE
```

大容量fileを勝手にGit LFS化しない。

---

# 13. Stage

全hard checks PASS後のみ:

```bash
git add .
```

その後:

```bash
git status --short
git diff --cached --stat
git diff --cached --name-only
```

を保存。

保存先:

```text
/home/nabe/projects/nankan-market-residual-phase2/docs/evidence/git_bootstrap/STAGED_FILES.txt
/home/nabe/projects/nankan-market-residual-phase2/docs/evidence/git_bootstrap/STAGED_DIFF_STAT.txt
```

これらaudit files自身もstageする。

---

# 14. Pre-commit final hard check

実stage済みfilesに対し再度:

```bash
git diff --cached --name-only
```

でpublic禁止path/extensionが0件であることを確認。

さらに次を記録:

```text
tracked candidate file count
total staged byte size
files >1 MiB
```

---

# 15. First commit

全check PASS後:

```bash
git commit -m "chore: bootstrap Phase2 source repository"
```

commit後:

```bash
git rev-parse HEAD
git status --porcelain
git branch --show-current
```

`git status --porcelain` は空であること。

---

# 16. Bootstrap report

作成:

```text
/home/nabe/projects/nankan-market-residual-phase2/docs/evidence/git_bootstrap/GIT_BOOTSTRAP_REPORT.md
```

内容:

```text
STATUS
repository root
branch
initial commit SHA
git identity configured: YES
tracked file count
tracked total size
files >1 MiB
public-boundary violations: 0
secret-scan violations: 0
DB tracked: NO
processed data tracked: NO
OOF/output tracked: NO
audit runtime tree tracked: NO
Keibabook tracked: NO
model binaries tracked: NO
worktree clean: YES/NO
remote configured: YES/NO
```

このreportを追加するための2nd commitは不要。
**GIT_BOOTSTRAP_REPORT.mdをfirst commitに含めたい場合、commit前に生成してstageすること。**
そのため実装上はcommit直前にtemporary placeholderではなく実値を生成する。

commit SHAだけはcommit前に確定できないため、report内initial commit SHAは:

```text
SELF
```

とし、commit後のactual SHAはconsoleとrun reportに記録する。

---

# 17. Remote

このJobではremoteを新規追加しない。

`origin`が無いことは正常。

既にremoteがある場合のみURLを報告し、変更しない。

---

# 18. GitHub repo target

後続GIT002で接続予定:

```text
owner: fummyz-dot
repo: nankan-market-residual-phase2
visibility: PUBLIC
default branch: main
```

このJobではGitHub repo作成やpushを実行しない。

---

# 19. Prohibited

```text
rm -rf .git
git clean -fdx
git reset --hard
git add -f
git push
git remote set-url
Git LFS導入
DB/dataの移動
Job004 artifact削除
audit runtime treeのGit追加
```

---

# 20. Final console

```text
STATUS: GIT001_PASS | GIT001_BLOCKED_...

LOCAL:
- root:
- branch:
- initial commit:
- worktree clean:

TRACKED:
- file count:
- total bytes:
- >1MiB files:

SAFETY:
- DB tracked:
- processed data tracked:
- outputs tracked:
- audit runtime tracked:
- Keibabook tracked:
- model binaries tracked:
- secret matches:

REMOTE:
- origin:

NEXT:
Create/connect public GitHub repository and push main in GIT002.
```
