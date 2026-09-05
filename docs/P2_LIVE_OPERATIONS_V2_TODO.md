# P2 Live Operations V2 Managed Backlog

## Status and sizing

This is the authoritative V2 implementation backlog.  `OPEN` is ready for a
separately authorized implementation; `DESIGNED` has an approved contract but
may need its stated prerequisite; `DONE` is a retained regression, not work
to redo.  Valid statuses are `OPEN`, `DESIGNED`, `IMPLEMENTING`, `BLOCKED`,
`DONE`, and `DEFERRED`.

Estimated depth: `S` = bounded adapter/tests, `M` = one operational workflow, `L` = cross-module lifecycle change.

## Current operational priority (2026-09-02 post-live)

The highest-priority next work is **design only**:
`P2-RACE-DAY-PERFORMANCE-DESIGN`.  Before any Codex optimization task, User +
ChatGPT must inspect the source critical path, use WIDE stage-timing evidence,
set LIVE latency budgets, describe the processing DAG, and freeze safe versus
unsafe concurrency/caching, numerical/scientific invariants, and failure
behavior.  The 11R observation (16 runners / 120 WIDE pairs; roughly 669
seconds from T15 to Experimental decision and roughly 260 seconds to post)
is a measured input to that design, not grounds for speculative optimization.

Lower-priority backlog, without promotion from one live day: optional
historical 2026-08-21 bodyweight recovery, phone notification, further WIN
near-miss accumulation, WIN↔WIDE coherence accumulation, and CURRENT/Training
prospective context accumulation.

## P0

### OPSV2-001

- ID: `OPSV2-001`
- Priority: `P0`
- Status: `DONE`
- Problem: Collector and CLI outcomes collapse expected business skips, nonblocking capture issues, and technical failures; Primary exclusion currently surfaces as an exception.
- Resolution: `P2_RACE_DAY_OUTCOME_EXIT_CONTRACT_V1` now provides the
  application 0/10/20 contract and exactly one final `RACE_DAY_OUTCOME`
  block.  `P2_RACE_DAY_COMPACT_POST_WAITING_HOTFIX_017` preserves compact
  rendering for valid wait payloads.  Feature/model semantics are unchanged.
- Retained acceptance: expected healthy outcomes exit 0, recoverable wait or
  block exits 10, and invariant failures exit 20.

### OPSV2-002

- ID: `OPSV2-002`
- Priority: `P0`
- Status: `DEFERRED`
- Resolution/status: the bounded raw/parser diagnosis and missing-change
  Hotfix-012 established the historical parser form.  Rebuilding the old
  failed artifacts is optional historical recovery backlog; it is not a
  current live-operation blocker and must not outrank performance design.

### OPSV2-003

- ID: `OPSV2-003`
- Priority: `P0`
- Status: `DESIGNED`
- Problem: Static blockers are currently discovered through ad-hoc per-race checks, sometimes too near T15.
- Design requirement: Add one all-race published-card static preflight with identity, direction, V1 person compatibility, class, artifacts, raw/normalized freshness, collision, and Keibabook context status.
- Acceptance: A venue/day report lists every race, Primary classification, static readiness/blockers, and `CONTEXT_INCOMPLETE` separately; no individual 9R/10R/11R command is required.
- Dependencies: OPSV2-001; retained R1/R7/R13/P7 checks.
- Estimated implementation depth: `M`.

### OPSV2-004

- ID: `OPSV2-004`
- Priority: `P0`
- Status: `DESIGNED`
- Problem: Normal operation exposes multiple internal maintenance commands and hides next-action timing.
- Design requirement: Implement `./race-day --date --venue` as a thin orchestrator for the ordered A–O lifecycle in the design contract, with automatic shadow inference only for eligible Primary races.
- Acceptance: One command owns prior history update, preflight, collection, T15 validation, eligible inference/bundle, result monitoring/collection, reconciliation, ledger handling, and prints next action/time.
- Dependencies: OPSV2-001, OPSV2-002, OPSV2-003.
- Estimated implementation depth: `L`.

### OPSV2-005

- ID: `OPSV2-005`
- Priority: `P0`
- Status: `DESIGNED`
- Problem: Frozen decisions currently require manual Python/JSON construction with internal hashes and references.
- Design requirement: Have race-day/race-shadow create a validated decision draft and implement `./race-decision` with only `BET|NO_BET`, tickets, and stake input.
- Acceptance: A valid bundle produces an immutable decision without user-entered SHA/model IDs; conflicting reruns reject overwrite and `NO_BET` records zero stake/profit.
- Dependencies: OPSV2-004; existing frozen prediction and ledger contracts.
- Estimated implementation depth: `M`.

### OPSV2-006

- ID: `OPSV2-006`
- Priority: `P0`
- Status: `DESIGNED`
- Problem: Actual user purchases can be confused with the strategy being evaluated.
- Design requirement: Store and reconcile frozen recommended portfolios separately from optional actual bets, with ticket-level stake and derived recommended payout/profit/ROI.
- Acceptance: Accounting uses only pre-race frozen recommended tickets; `NO_BET` gives stake/profit zero and ROI `N/A`; actual bets cannot alter primary strategy metrics.
- Dependencies: OPSV2-005; OPSV2-011; existing reconciliation path.
- Estimated implementation depth: `M`.

## P1

### OPSV2-007

- ID: `OPSV2-007`
- Priority: `P1`
- Status: `DONE`
- Resolution: CURRENT uses an official same-runner `/kis_info/<id>.do` anchor
  for current jockey identity and `P2_CURRENT_JOCKEY_CONTEXT_V2` for
  last-Nankan actual-start comparison.  Missing/ambiguous identity is
  `UNKNOWN`, never changed; CUR03 remains registered/not activated and
  nonblocking for Main.

### OPSV2-008

- ID: `OPSV2-008`
- Priority: `P1`
- Status: `DONE`
- Resolution: `P2_RESULT_COMPLETENESS_STATE_CONTRACT_V1` persists independent
  result-source, model-history, and per-ticket payout axes.  Repeated same-SHA
  retrieval is idempotent despite local archive-path variance; semantic
  conflicts remain fail-closed.  `DAY_COMPLETE_HISTORY_PENDING` is a
  recoverable exit-10 projection, not a change to scientific completion.

### OPSV2-009

- ID: `OPSV2-009`
- Priority: `P1`
- Status: `OPEN`
- Problem: Live rare source forms are still found reactively on race day.
- Design requirement: Perform a bounded South Kanto Main historical/source vocabulary audit for identity annotations, affiliation prefixes, result statuses, race-type text, and person display forms.
- Acceptance: Each observed pattern is covered by an approved existing semantic, a bounded new recovery task, or an explicit blocker; no nationwide generalization is added.
- Dependencies: Retained R5/R7/R8/R9/R10/P7 contracts.
- Estimated implementation depth: `M`.

### OPSV2-010

- ID: `OPSV2-010`
- Priority: `P1`
- Status: `DESIGNED`
- Problem: The analysis bundle does not yet enforce every human-checkable current/context quality field.
- Design requirement: Version the bundle handoff contract to require Market, Candidate, edge, fair odds, bodyweight/change, current/previous jockey/change, training, ability, warnings, cold start, source boundary, and decision deadline; emit unresolved rather than fabricated context.
- Acceptance: A bundle fixture contains every required field or explicit status, and result/winner/payout fields remain absent before decision.
- Dependencies: OPSV2-007; existing P8 bundle contract.
- Estimated implementation depth: `S`.

### OPSV2-011

- ID: `OPSV2-011`
- Priority: `P1`
- Status: `DONE`
- Problem: Result collection attempted to register a second key for an existing decision race and rolled back on the natural-key unique constraint.
- Design requirement: Retain natural-key resolution (`race_date + venue + race_number`) before registry insert; reuse canonical race key and block meaningful metadata conflicts.
- Acceptance: 2026-08-21 9R, 10R, and 11R collect/reconcile with existing registry keys and rerun as `IDEMPOTENT_NOOP`; integrity checks stay clean.
- Dependencies: Existing regression tests and collector contract.
- Estimated implementation depth: `S` (completed).

### OPSV2-012

- ID: `OPSV2-012`
- Priority: `P1`
- Status: `DONE`
- Problem: T15 materialization was not wired to the approved pre-race identity resolver.
- Design requirement: Retain resolver priority: official card/detail, genuine cold start, then exact official pedigree crosswalk; prohibit name-only and result-page identity sources.
- Acceptance: 2026-08-21 Kawasaki 1R #1 and 9R/10R/11R static fixtures resolve with zero unresolved identity; pre-race result DB access stays zero.
- Dependencies: R1/R7 identity contracts and regression tests.
- Estimated implementation depth: `S` (completed).

## P2

### OPSV2-013

- ID: `OPSV2-013`
- Priority: `P2`
- Status: `DESIGNED`
- Problem: Prospective operations need transparent venue/day monitoring without creating premature venue gates.
- Design requirement: Report Ohi/Funabashi/Kawasaki/Urawa race count, Candidate LL, Market LL, delta LL, BET count, recommended stake, recommended P/L, and recommended ROI as monitoring only.
- Acceptance: Reports segment by venue without changing eligibility, threshold, or automatic betting policy.
- Dependencies: OPSV2-006; sufficiently reconciled prospective records.
- Estimated implementation depth: `M`.

### OPSV2-014

- ID: `OPSV2-014`
- Priority: `P2`
- Status: `DEFERRED`
- Problem: Users need future phone notifications but delivery integration is not required for the operating contract.
- Design requirement: Expose durable `ANALYSIS_READY`, `BLOCKING_FAILURE`, and `DAY_COMPLETE` events with stable payloads; defer phone provider integration.
- Acceptance: Event hooks can be consumed by a later notifier without changing race-day lifecycle semantics.
- Dependencies: OPSV2-004.
- Estimated implementation depth: `S`.

### OPSV2-015

- ID: `OPSV2-015`
- Priority: `P2`
- Status: `DESIGNED`
- Problem: Development concerns and fixed-version daily operations are intermixed operationally.
- Design requirement: Define a handoff package separating development artifacts/parser experiments from operations version, runbook, preflight, ledger, and support interfaces.
- Acceptance: The package documents ownership and version boundaries without becoming a prerequisite for normal V2 operation.
- Dependencies: OPSV2-004, OPSV2-006.
- Estimated implementation depth: `S`.
