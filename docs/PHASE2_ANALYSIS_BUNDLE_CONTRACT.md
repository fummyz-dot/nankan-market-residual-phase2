# Phase 2 Race Analysis Bundle Contract

## Purpose
`p2_race_analysis_bundle_v1` is a machine-readable handoff of one prospective race's approved, as-of inputs and data-quality status. It is not a prediction, probability, edge calculation, ticket, result, or payout record.

## Source boundaries
- `p2_main`: approved current bodyweight and explicitly selected pre-race market data only.
- `p2x_o`: Keibabook fields classified as `EXT_OBJECTIVE` by the A01 classification artifact. It remains external and `NOT_MODEL_FEATURE_YET`.
- `p2x_s`: Keibabook training as `RAW_STRUCTURED_EXTERNAL` and `NOT_MODEL_FEATURE_YET`.

These namespaces are separate objects and must never be flattened into one feature namespace.

Keibabook ability past rows carry a conservative `past_event_type`: `TRIAL` and `RETRAINING_TRIAL` are explicitly tagged when their labels say so; rows without confirmed event semantics remain `UNKNOWN`. No trial/retraining-trial row is promoted to normal official-race history or to a model feature in this bundle.

## As-of rule
The builder selects the requested snapshot role explicitly. For the current engineering baseline, it requires exactly one `PRIMARY_CANDIDATE` mark with `target_decision_time=T-15_ENGINEERING_CANDIDATE`; it never uses `MAX(captured_at)` or a later secondary capture. T-15 is not frozen.

## Eligibility
The bundle records the draft operational eligibility decision. `ELIGIBLE`, `INELIGIBLE`, and `REVIEW_REQUIRED` are input-operation states, not final-holdout eligibility protocol states.

## Prohibited data
The bundle must contain no current-race result, finish position, winner, payout, payback, settled return, final odds, or post-primary snapshot. Keibabook fields `RT`, `CPU予想`, `展開予想`, `単勝オッズ`, `過去走人気`, and `raw_text` are prohibited from sanitized sections.

## Model and ticket status
Until a later approved job implements them, `models.status` is `NOT_AVAILABLE` and `ticket_candidates.status` is `NOT_AVAILABLE` with `MODEL_NOT_BUILT`. Market inverse odds are not called a model prediction.

## Provenance and immutability
Every bundle records selected capture/snapshot IDs, raw hashes, Keibabook raw hashes, source paths, code/config manifest hashes, and a content hash. Output is append-only by default: a same-path rebuild fails unless an explicit deterministic-rebuild mode is selected and recorded.

## ChatGPT handoff
Keep `race`, `eligibility`, `decision`, `data_quality`, `models.status`, and warnings near the top level. Uploading a bundle is a data handoff only; it does not authorize automated betting or change the research protocol.

## Future one-command wrapper (not frozen)
A future wrapper may perform race URL parsing, eligibility, Keibabook discovery, bodyweight/market collection, quality validation, feature/model inference, and bundle construction. Its command, model inference, and primary decision time remain unapproved and unfrozen.
