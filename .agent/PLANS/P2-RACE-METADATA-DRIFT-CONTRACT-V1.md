# P2-RACE-METADATA-DRIFT-CONTRACT-V1

## Job metadata

- Job ID: P2-RACE-METADATA-DRIFT-CONTRACT-V1
- Title: Separate canonical race identity from mutable race metadata
- Status: COMPLETE
- Owner: Codex

## Objective

Replace the prospective collector's whole-race-metadata equality gate with an
explicit field contract: hard material drift blocks; approved mutable and
presentation drift continues with durable field-level evidence; scheduled-post
drift continues but cannot become a T15 scientific sample.

## Inputs and invariants

- Existing official collector/parser, capture checkpoint, event, raw archive,
  market/current snapshot, and fallback resolver primitives only.
- Canonical race identity is date, venue, and race number, as independently
  URL/page-validated by the official adapter.
- Hard drift must fail closed.  Unknown fields must fail closed.
- `field_size` may continue only through the existing runner/withdrawal and
  WIN/WIDE roster/capture-set integrity checks.
- A changed official scheduled post never rewrites the immutable day plan or
  capture schedule; its accepted capture is recorded as the existing
  `PRE_RACE_FALLBACK`, with no scientific T15 status.
- No result endpoint or post-hoc 2026-08-28 capture/promotion is allowed.

## Change surface

1. Add narrow metadata classification/diff helpers beside the collector and
   use them only in `_capture`.
2. Persist the resulting field-level drift evidence using existing event,
   checkpoint/capture, and raw archive mechanisms; no schema/table is added.
3. Carry the scheduled-post drift state through existing fallback/combined
   snapshot fields without changing model, policy, FS04, or plan semantics.
4. Add focused collector regressions and a fresh-process fixture smoke.

## Acceptance tests

- No drift preserves existing T15 standard behavior.
- Field-size and presentation drift continue; roster/WIN/WIDE checks remain
  mandatory.
- Scheduled-post drift produces `PRE_RACE_FALLBACK`, `scientific_sample=false`,
  `fallback_reason=SCHEDULED_POST_TIME_DRIFT`, and keeps the plan unchanged.
- Distance, surface, material class/conditions, and unknown drift block while
  persisting old/new field evidence.
- Existing withdrawal, unseen-person, ambiguity, capture-set, and
  Recommendation Evidence semantics pass.
- Fresh process uses saved fixtures plus a temporary DB and has
  `result_db_accessed=0`.

## Exclusions

No generic identity framework, service/layer, new table/CLI/dependency,
fuzzy/name fallback, refactor, policy/model/FS04/DEV-LIVE-V1 change, or writes
to 2026-08-28 Recommendation/analysis/research evidence.

## Required audit

Write `audit/data/p2_race_metadata_drift_contract_v1/` with a `vcs_mode:none`
run manifest, source/config/input hashes, tests/smoke, exact metadata
inventory, and before/after frozen-Evidence hashes.

## Completion

Implemented only the local collector metadata contract and the existing
pre-race reference selector connection needed for a timing-drift T15 to remain
an explicit non-scientific fallback.  Field-level old/new evidence is emitted
through the existing event ledger; successful captures retain it in existing
source/snapshot `notes`, and failed material/unknown drift retains the official
raw archive path in the event.  No day-plan, policy, schema, model, FS04, or
2026-08-28 Evidence artifact was modified.
