# P2-WIN-MARKET-TRAJECTORY-V1-001 — fixed-time WIN market research ledger

## Scope

Create a read-only-on-source, append-only research sidecar over the existing
collector's WIN `market_snapshots`.  It will record only explicitly marked
T20/T15/T10/T05/RECOVERY captures, materialize deterministic trajectory
summaries, and run from race-day without delaying or changing Main analysis.
No collector, model, feature, policy, recommendation, stake, result, or
actual-bets path is changed.

## Inputs

- Existing `market_snapshot.sqlite`: `race_registry`, `source_captures`, and
  WIN `market_snapshots` are the sole capture authority.
- Existing `normalize_win_odds`, DEV-LIVE-V1 frozen gamma, and market-offset
  zero-residual assembly reproduce the live `q_raw` and calibrated Market
  probability; no separate gamma is fitted.
- Existing Recommendation Evidence is read only for an optional exact T15
  Main-bundle reference.  It is never required to collect a trajectory.

## State / durability

- One immutable `win_market_trajectory_mark_events` row is recorded per
  observed `(race, version, explicit mark, capture_id)`.  A repeat is an
  idempotent no-op; a same mark with a different capture remains append-only
  and produces an explicit ambiguous-mark research state rather than an
  arbitrary replacement.
- `win_market_trajectory_evidence` is a deterministic materialized view keyed
  by `(race, version)`.  It can be regenerated from only pre-race mark events;
  its overwrite is permitted solely because the immutable event set is the
  authority.  Its event-set SHA blocks silent source replacement.
- At/after scheduled post, materialization may read already archived snapshots
  but rejects any capture at/after post and never fetches a new snapshot.

## Time / roster semantics

- Standard marks are accepted only when source-capture `notes.mark` exactly
  names the mark and the collector wrote a complete, pre-post WIN roster.
  RECOVERY is always retained under its own mark and is never relabeled.
- Mark events store capture/snapshot/raw hashes, capture and post timestamps,
  seconds-to-post, complete active roster, and all per-runner probabilities.
- Deltas cover the roster union.  A runner absent at the later mark gets
  `RUNNER_WITHDRAWN_BEFORE_LATER_MARK`; it is never silently intersected away.

## Main and result boundaries

- T15 candidate/edge references are added only when the committed Main bundle
  itself is `T15_STANDARD` and names the exact T15 market capture.  T10/T05
  diagnostics retain that fixed T15 candidate only; they cannot re-decide.
- The sidecar imports no result/evaluator/settlement module and reads no result
  table before or after post.  A failure produces only a trajectory research
  event/status and cannot block Main, WIN research, WIDE research, collection,
  or result processing.

## Tests / acceptance

- Unit/fresh-process fixtures cover full/partial/recovery trajectories,
  withdrawal and stable rosters, delta and entropy mathematics, Main-edge
  references, restart/idempotency, post-time rebuild/no-new-capture rejection,
  Main/WIN/WIDE coexistence, and a zero pre-race result-access audit.
- Freeze protocol/model artifacts, audit artifacts, run manifest, and a
  race-day compact sidecar status are required before completion.
