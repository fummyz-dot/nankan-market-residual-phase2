# Codex作業指示
## GIT001B — Empty `.git` Recovery and Local Bootstrap Resume

IMPLEMENTATION MODEL: Sol

### Duration class
`SHORT`

### Repository root

```text
/home/nabe/projects/nankan-market-residual-phase2
```

---

# 1. Purpose

GIT001で検出された、

```text
/home/nabe/projects/nankan-market-residual-phase2/.git
```

が「存在するがGit repositoryではなく、空ディレクトリ」である状態を安全に解消し、
GIT001の残りを再開する。

---

# 2. Empty `.git` hard verification

まず以下を実行し、`.git` が本当に空であることを確認する。

```bash
cd /home/nabe/projects/nankan-market-residual-phase2

test -d .git
find .git -mindepth 1 -maxdepth 1 -print
```

さらに:

```bash
find .git -type f -o -type l -o -type d
```

を確認する。

許容状態:

```text
.git 自身以外のentry = 0
regular file = 0
symlink = 0
subdirectory = 0
```

1件でも中身があれば削除禁止。

その場合:

```text
STATUS: GIT001B_BLOCKED_NONEMPTY_GIT_DIR
```

で停止。

---

# 3. Empty `.git` の削除

上記hard verificationをPASSした場合のみ:

```bash
rmdir /home/nabe/projects/nankan-market-residual-phase2/.git
```

を実行してよい。

**許可する削除コマンドは `rmdir` のみ。**

禁止:

```text
rm -rf .git
rm -r .git
find .git -delete
```

`rmdir` が失敗したら、それ以上削除操作を行わずBLOCKする。

---

# 4. Git initialization

空 `.git` の `rmdir` 成功後:

```bash
cd /home/nabe/projects/nankan-market-residual-phase2
git init -b main
```

確認:

```bash
git rev-parse --is-inside-work-tree
git branch --show-current
```

Expected:

```text
true
main
```

---

# 5. Resume original GIT001

ここからは既存:

```text
/home/nabe/projects/nankan-market-residual-phase2/docs/jobs/GIT001_LOCAL_BOOTSTRAP.md
```

の **Step 8以降** をそのまま実行する。

つまり:

1. Git identity確認
2. `git add -n .`
3. public-data boundary check
4. secret scan
5. file-size audit
6. actual stage
7. staged final hard check
8. first commit
9. commit SHA取得
10. worktree clean確認
11. bootstrap report作成

既存GIT001の禁止事項・public safety ruleはすべて維持。

---

# 6. Bootstrap files

必須存在確認:

```text
/home/nabe/projects/nankan-market-residual-phase2/.gitignore
/home/nabe/projects/nankan-market-residual-phase2/docs/GIT_POLICY_V1.md
/home/nabe/projects/nankan-market-residual-phase2/docs/GIT_WORKFLOW_V1.md
/home/nabe/projects/nankan-market-residual-phase2/docs/jobs/GIT_BOOTSTRAP_AFTER_JOB004.md
/home/nabe/projects/nankan-market-residual-phase2/docs/jobs/GIT001_LOCAL_BOOTSTRAP.md
```

不足があれば停止。

---

# 7. Public-safety boundary

以下は絶対にtrackedにしない:

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
.venv-p2-model/
wheelhouse/
*keibabook*
*.sqlite
*.db
*.csv.gz
*.cbm
*.pkl
*.pickle
*.joblib
*.onnx
*.pt
*.pth
```

`git add -f`は禁止。

---

# 8. First commit

GIT001の全hard check PASS後のみ:

```bash
git commit -m "chore: bootstrap Phase2 source repository"
```

取得:

```bash
git rev-parse HEAD
git status --porcelain
git branch --show-current
```

Expected:

```text
branch = main
worktree = clean
```

---

# 9. Remote

このJobではremote追加・pushをしない。

禁止:

```text
git remote add
git remote set-url
git push
```

---

# 10. Final console

```text
STATUS: GIT001_PASS | GIT001B_BLOCKED_...

EMPTY GIT RECOVERY:
- .git existed:
- .git empty:
- removal method:
- rmdir success:

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
Proceed to GIT002 remote connection and push.
```
