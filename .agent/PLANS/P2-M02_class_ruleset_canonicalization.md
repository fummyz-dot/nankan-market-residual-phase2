# P2-M02 — Official Class Ruleset & Race Condition Canonicalization

## Status
COMPLETED

## Inputs
- Read-only `db/p2_history_context.sqlite` (South Kanto rows only)
- Immutable official South Kanto source archives under `data/raw/official_rules/nankan_class/`
- P2-A01 class profiles and P2-M01 provenance artifacts

## Invariants
- No M01 DB modification; no other-flat South Kanto mapping.
- Ruleset assignment is date/age scoped and is evidence-backed: legacy, 2023 2YO pilot, and 2024 all-horse points.
- Exact current class vocabulary is ordinal only; it is not continuous strength.
- No historical program points/boundary positions are fabricated.
- No performance, market, payout, or outcome field participates in mapping.

## Outputs and acceptance
Produce the versioned mapping/ruleset registries, official source manifest, one Nankan-race curated CSV, audits, tests, contract, report, and Gitless run manifest. Success requires zero unresolved rows that contain explicit A1–C3 tokens and no mapping of other-flat rows.

## Completion record
- Four official sources were archived with hashes. The 2023-04-01 2YO pilot and 2024-01-01 all-horse transition are `OFFICIAL_CONFIRMED`.
- All 21,849 South Kanto races were output; explicit class-token unresolved and other-flat mappings are both zero.
- Legacy threshold reconstruction and historical as-of program points remain explicitly unavailable.
