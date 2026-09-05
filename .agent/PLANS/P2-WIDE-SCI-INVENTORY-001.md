# P2-WIDE-SCI-INVENTORY-001 — Read-only WIDE science inventory

## Objective

Mechanically inventory development-period WIDE market, labels, pre-race
timestamps, V1 WIDE semantics, WIN OOF/walk-forward predictions, and
pair-level inputs.  Produce an auditable readiness assessment without
training, regenerating OOF predictions, altering implementation semantics,
or reading operational result values for model selection.

## Inputs

- Repository source, schemas, manifests, and pre-existing artifacts.
- Development historical data through `2026-07-31` only.
- Operations records from `2026-08-21` onward only for engineering snapshot
  inventory, explicitly excluded from model-selection counts.

## Outputs

- `audit/data/p2_wide_science_inventory_20260825/` with the six requested
  inventory/readiness artifacts plus a run manifest.

## Invariants and exclusions

- Read-only source inspection; no production database writes or mutations.
- No model fitting, OOF regeneration, calibration, feature construction,
  parameter selection, threshold changes, or performance comparisons using
  August outcomes.
- `MARKET_TIME_UNKNOWN` is never promoted to actual pre-race.
- Full-model or in-sample predictions are never classified `OOF_SAFE`.

## Method

1. Map source code, schemas, artifacts, and the V1 read-only implementation.
2. Profile development WIDE market, label, feature, and OOF availability at
   race and pair grain; separately profile operational actual snapshots.
3. Trace V1 WIDE probability/loss/market semantics from implementations and
   frozen artifacts.
4. Join only development-period, documented keys to quantify OOF-ready
   coverage and report regeneration prerequisites if OOF is absent.
5. Write deterministic artifacts and a provenance/run manifest; verify source
   files and production DBs were not changed.

## Acceptance

- Required requested artifact files exist and contain counts, classifications,
  source-symbol references, semantic inventory, readiness matrix, and known
  limitations.
- Leakage classifications and development/operations separation are explicit.
