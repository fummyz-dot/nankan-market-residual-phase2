# P2-M06 — V1 Legacy Semantic Port & Unified Historical Feature Matrix

## Inputs

- Read-only `reference/v1/` WIN V1 schema, contract, builder, and saved feature matrix.
- Phase 2 historical context DB and frozen P2 Class, Speed, and Pace artifacts.

## Outputs

- Active `src/features/legacy_v1/` implementation and exact 119-feature registry.
- A 250,093-row legacy feature artifact, unified model matrix and metadata,
  frozen lineage/feature-set manifests, audits, contracts, and report.

## Invariants

- V1 references are specification/parity inputs only; no reference runtime import.
- V1 artifact overlap uses exact feature parity; no rounding tolerance above
  `1e-12` is allowed.
- The V1 saved matrix has 245,208 starter-only rows. M06 keeps that as the
  parity universe and extends the same label-free, date-block transformation
  to the full 250,093 historical-development roster. This extension is not
  represented as V1-result reinterpretation.
- Every historical state source is strictly earlier than the target calendar
  date. Current outcomes/bodyweight, same-day/future rows, last-seen metadata,
  Market, Keibabook, P2_BIAS, P2_CURRENT, and P2_EXT are excluded.
- All joins are one-to-one with no row loss or expansion. Eligibility is
  metadata only; labels are physically absent.

## Acceptance

- 119 V1 features with frozen F0/F1/F2/F3/F5/F6/F7/F8 groups.
- Available V1-reference parity passes; 250,093 unified rows cover 21,849
  races through 2026-07-31; deterministic rebuild passes.
- FS00–FS04 are frozen before any model or Market performance work.

