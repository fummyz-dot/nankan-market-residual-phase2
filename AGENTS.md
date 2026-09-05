# AGENTS.md — NANKAN_MARKET_RESIDUAL_PHASE2

## Mission
This repository develops a South Kanto (Ohi, Funabashi, Kawasaki, Urawa) horse-racing model whose final operational output is a bet recommendation shown approximately 10–20 minutes before scheduled post time. The user executes purchases manually.

The research target is not generic prediction accuracy. The primary scientific question is whether a frozen candidate improves a calibrated pre-race market baseline and whether that incremental probability edge can support a pre-registered mid-odds betting rule.

Primary operational goal:
Enable model-based manual betting during September 2026 if live-development evidence is sufficient.

Always optimize the critical path toward prospective shadow prediction and manual betting.
Do not introduce repeated or long-running validation gates unless they are necessary.
Before every new stage, evaluate whether it can be skipped, reused, parallelized, or deferred.

## Project boundary
- This is a new Phase 2 project. Do not modify or reinterpret V1 results.
- V1 remains `NAR_ONLY_PROJECT_GIVEUP` within its preregistered search space.
- All copied V1 assets under `reference/v1/` are immutable read-only reference inputs.
- Never write into the original V1 repository or copied reference directories.
- Data from 2026-07 and earlier may be used only for development, engineering, semantic audits, feature construction, model selection, and power simulation. It is not a Phase 2 final holdout.

## Codex progress reporting policy
- 進捗報告は原則として日本語で行う。
- トークン節約のため、途中経過は必要最小限にする。
- 通常の進捗報告は1〜3行程度とする。
- 長時間Jobでは、原則として以下のタイミングだけ報告する。
  - 開始時
  - 主要phaseの切替時
  - おおよそ25% / 50% / 75%到達時
  - warning / error / block発生時
  - 完了時
- 詳細な監査情報、checkpoint、統計値、ログはchatへ大量出力せず、
  CSV / JSON / Markdown / log等のartifactへ保存する。
- 最終回答は簡潔にし、主に以下を含める。
  - STATUS
  - 主要結果・主要数値
  - WARNINGS
  - NEXT
- 長い内部処理説明や、逐次のファイル操作説明は不要。
- ユーザーが明示的に詳細な途中経過を要求した場合のみ例外とする。

## Non-negotiable research rules
1. No current-race result, payout, final odds, or post-decision snapshot may enter model features.
2. Availability is governed by `available_at <= decision_time`.
3. `T15_STANDARD` is the primary scientific pre-race reference mark for current prospective evaluation. Approved pre-race fallback operation is a separate semantic (`PRE_RACE_FALLBACK`) and must not be silently mixed into the Primary T15 scientific sample. T10/T05 observations are future relative to T15 and may be used only by explicitly approved diagnostic research, never as T15 prediction or recommendation inputs.
4. Do not tune venue, odds band, threshold, decision time, feature definition, model family, or search budget after viewing final holdout results.
5. Primary evaluation is ALL four venues. Venue/segment analyses are secondary diagnostics only.
6. Probability model and bet-selection logic must remain separate.
7. WIN, WIDE, and TRIO are separate hypotheses. WIN is an engineering/methodology gate, not a statistical prerequisite for WIDE/TRIO edge.
8. Keibabook data is collected from the start but remains an external incremental experiment unless an approved protocol explicitly promotes it.
9. Search budgets must be recorded before experiments and consumed explicitly.
10. Any result-changing protocol amendment after freeze requires a new version and a new untouched holdout.

## Current data facts
- V1 `nankan_market.sqlite.odds_snapshots` exists but has zero rows. There is no confirmed historical actual pre-race snapshot collector.
- Monthly official odds from 2026-03 to 2026-07 are `MARKET_TIME_UNKNOWN` and are development references only.
- NAR history has runner-level `last_3f`, race-level `lap_times_json`, race-level `corners_json`, raw class/condition text, prize fields, and post time.
- NAR does not currently provide a confirmed model-ready runner-level first-3F or runner-level normalized corner-position table.
- Canonical class hierarchy, class strength, and class delta do not exist yet and are Phase 2 work.
- Keibabook ability JSON can contain structured past-runner first-3F, last-3F, pace, and corner positions; market/prediction fields must remain excluded.
- Keibabook training JSON contains workout dates, course, condition, load, time cells, notes, and paired-work text.

## Operational contract
- The user may provide Keibabook training/ability JSON early on race day.
- Normal live-day UX is `./race-day`.
- The race-day orchestrator owns approved official pre-race collection, fixed-mark market capture, recommendation generation, research shadows, resume behavior, and post-race continuation.
- `T15_STANDARD` is the normal scientific reference. An explicitly classified `PRE_RACE_FALLBACK` may preserve operations but remains scientifically separate.
- Do not create an alternate normal live CLI or require manual source-URL orchestration unless an explicit task changes this contract.
- Purchases are manual and outside this repository's execution scope.
- A page may contain prohibited fields such as odds even when used for body weight. Raw capture may be retained, but the P2_CURRENT parser must whitelist allowed fields and quarantine/ignore market fields.

## Source separation
Feature namespaces must remain explicit:
- `V1_F0-F8`: frozen legacy feature semantics.
- `P2_CLASS`: class normalization/strength/delta.
- `P2_SPD`: strict-as-of speed/standard-time features.
- `P2_PACE_NAR`: NAR-only pace/section/corner information.
- `P2_CURRENT`: current pre-race information proven available by decision time.
- `P2_MKT`: actual pre-race market snapshot and approved pre-decision trajectory.
- `P2_EXT_ABILITY`: Keibabook objective ability-table additions.
- `P2_EXT_TRAINING`: Keibabook workout additions.

## Design authority and source discipline

This repository separates design authority from implementation execution.

### Design authority

Scientific reasoning, model policy, protocol semantics, architecture decisions,
metric definitions, eligibility rules, thresholds, frozen-model behavior, and
other result-changing design decisions are defined outside Codex and must be
provided explicitly in the task.

Codex is an implementation and verification executor.

Codex must not independently:
- invent or modify scientific semantics;
- choose alternative model, metric, threshold, feature, eligibility, fallback,
  retry, or state-transition semantics;
- reinterpret an explicitly frozen protocol;
- broaden a task because another design appears preferable;
- resolve material ambiguity by selecting one of several plausible behaviors.

If implementation requires a design decision that has not been explicitly
defined and different choices could change scientific, operational, or data
semantics, stop and report `BLOCKED`.

Do not implement a "reasonable default" for an unresolved design question.

### No external specification discovery by default

Do not use Web search, external documentation, external repositories, online
examples, or other Internet sources to infer or supplement project requirements
unless the task explicitly authorizes that external source.

The default sources of truth are only:
- this repository;
- explicitly named repository files;
- frozen manifests and artifacts;
- explicitly supplied fixtures or raw files;
- the exact task specification.

If required source semantics cannot be established from those sources, stop and
report `BLOCKED`.

The user or design authority may separately obtain external pages/files and
supply them as approved inputs.

### Inspect before escalating

If a requested implementation cannot be designed safely without inspecting
additional repository files, schemas, raw captures, artifacts, or logs, do not
guess and do not search the Web.

Report exactly which local evidence is required and why.

### Task specification precedence

For implementation details explicitly defined in the current task, implement
those details exactly.

Do not substitute a different implementation merely because it appears cleaner,
more generic, more modern, or more optimal.

Repository invariants and frozen protocols remain authoritative unless the task
explicitly changes them.

## Repository and version-control contract

This Project is Git-managed. The GitHub source of truth is
`fummyz-dot/nankan-market-residual-phase2`.

- `main` is the accepted/reviewed state.
- Normal Codex work uses a `codex/<job-id>` branch.
- Every job records its starting and ending commit SHA.
- Source/specification changes must be committed before any result-producing
  long run that depends on them.
- Local SQLite databases, processed data, outputs, and the root `audit/` runtime
  tree remain untracked.

When a task requires reporting changed files, report the paths actually written
during the task, distinguish created from modified paths, and include SHA-256
and executed validation when needed. Git diff/status may provide the tracked
write-set evidence, while runtime artifacts remain governed by their explicit
SHA-256 manifests.

## Codex working rules
Before editing:
1. Read this file.
2. Read the relevant policy files under `docs/`.
3. Read the job plan under `.agent/PLANS/` if one exists.
4. Inspect existing tests and reference artifacts before reimplementing V1 semantics.

For every job:
- Create/update a job plan before implementation when the task is multi-step.
- Define inputs, outputs, invariants, exclusions, and acceptance tests.
- Preserve source immutability.
- Prefer deterministic, auditable transformations.
- Emit a run manifest with `vcs_mode: git`, `implementation_git_commit` set to the exact commit used for execution, starting/ending commit provenance, workspace root, code/input/config manifest hashes, platform and Python/library versions, random seed, commands, and output artifacts.
- Add unit/integration/leakage tests appropriate to the change.
- Do not silently change research semantics to make tests or metrics look better.

## Process supervision
- Prefer foreground, synchronous, bounded, checkpointed work. Do not create unsupervised background workers merely for speed.
- When background/parallel work is necessary, use the documented supervisor contract: a worker is `RUNNING` only when supervisor and worker exist and heartbeat and progress are fresh.
- Persist worker PID, timestamps, progress, stdout/stderr, exit code, terminal status, failure reason, and last successful checkpoint. Any child failure makes the parent fail.
- Use separate atomic `RUNNING`, `COMPLETE`, and `FAILED` markers. `COMPLETE` requires every worker success; perform and record an orphan-process audit at closeout.

## Implementation design and testing policy
- For important implementations, Codex must not independently invent behavior that has not been explicitly defined.
- Before implementation, define at minimum:
  - inputs and outputs;
  - state transitions;
  - database transactions, foreign keys, and rollback behavior;
  - time semantics, timezone handling, and boundary conditions;
  - failure, retry, resume, and idempotency behavior;
  - duplicate handling, partial writes, and atomic promotion.
- A successful happy-path test alone is not sufficient for completion.
- Add failure-injection tests where relevant, including:
  - network failures;
  - database / foreign-key failures;
  - parser failures or schema drift;
  - partial writes;
  - interrupted execution and resume;
  - duplicate records;
  - timestamp-boundary cases;
  - missing parent or child records.
- Test quality is judged by whether the tests can detect realistic expected failure modes, not by the number of tests.
- Before implementation, identify important failure modes and define test cases that detect them.
- If source semantics or required behavior are unclear, do not guess. Stop and report `BLOCKED`.
- Before real-data operation, pass appropriate unit, integration, leakage, and fixture tests.
- During the first real-world run of a new operational component, perform an early smoke test. Avoid designs where failures may remain unnoticed until the end of a long-running job.
- Keep success, failure, missed, warning, and other terminal states explicitly separated.
- A failed operation must never be recorded as a successful artifact or `COMPLETE`.
- When fixing a defect, add a regression test that reproduces the failure and prevents the same class of defect from recurring.

### LIVE time-sensitive performance design

For difficult, high-cost, long-running, or runtime-sensitive LIVE work,
Codex must not be asked to implicitly design and optimize the architecture in
the same implementation request.  Before a Codex implementation task, User +
ChatGPT must first freeze a detailed design that states the computational
complexity where relevant, processing order/DAG, explicit performance budget,
safe and unsafe concurrency/caching boundaries, numerical/scientific identity
invariants, and failure behavior.  The work is then divided into narrow
implementation tasks.  This is mandatory for LIVE time-sensitive paths.

## Frozen prospective artifacts

A model, policy, research arm, calibration parameter, threshold, or science
spec marked frozen must not be retrained, retuned, searched, rewritten, or
semantically changed before its registered review milestone unless an explicit
new-version protocol is approved.

Engineering fixes may preserve frozen behavior but must prove invariance of
affected frozen outputs where relevant.

Prospective observations must not be used to opportunistically tune a frozen
candidate.

## Hard stop conditions
Stop and report instead of guessing when:
- source semantics are ambiguous in a way that could create leakage;
- a required timestamp cannot be established;
- a join has unexplained non-zero mismatches;
- a parser would need to infer a future or prohibited field;
- a requested change would alter a frozen protocol or exceed the registered search budget;
- a copied V1 asset would need modification.

## Definition of done
A job is not complete until:
- code/tests pass;
- data-quality and leakage audits pass;
- all output artifacts are written under Phase 2 namespaces;
- a run manifest exists;
- known limitations are documented;
- no V1 reference asset was modified.
