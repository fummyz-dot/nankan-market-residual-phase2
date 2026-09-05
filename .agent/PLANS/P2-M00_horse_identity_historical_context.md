# P2-M00 — 14-Venue Horse Identity & Historical Context Foundation

## Job metadata
- Job ID: P2-M00
- Title: 14-Venue Horse Identity & Historical Context Foundation
- Status: COMPLETED
- Owner: Codex

## Objective
Establish whether a raw-native, non-fuzzy horse identity safely joins pre-cutoff South Kanto target horses to other flat NAR history, without changing prediction targets or building features.

## Inputs
- `reference/v1/data/raw_nar/zips/race/` (read-only, 2020-01–2026-07 only)
- `reference/v1/db/nankan_history.sqlite` (read-only comparator)
- `reference/v1/tools/` (read-only semantics inspection)
- A01 14-venue audit artifacts.

## Invariants
- Target/loss/evaluation remain South Kanto four venues only.
- Ban'ei is excluded from flat history.
- Identity is raw-native only: no fuzzy matching and no name-only production promotion.
- `race_date < target.race_date` is the minimum future feature condition; same-day remains prohibited absent ordering evidence.
- Archive member provenance is retained. Rows after `2026-07-31`, including the 128 history-DB rows, are excluded.

## Allowed modifications
- `src/audit/`, `tests/`, `docs/`, `audit/data/p2_m00/`, `reports/development/`, `data/manifests/`, and this plan.
- A schema draft only for `db/p2_history_context.sqlite`; do not create a full context build unless identity is established.

## Tasks
1. Profile raw racelist/horselist schema variants, venue vocabulary, candidate identifiers, and V1 horse-key construction.
2. Foreground sequentially process raw monthly ZIP members with annual checkpoints and source hashes.
3. Evaluate raw-native composite identity safety, collisions, stability, target universe, and other-flat history completeness.
4. Write identity/context contracts, audits, tests, report, and Gitless run manifest.

## Exclusions
- No model training, market/residual evaluation, ROI, odds use, venue/model search, class/speed performance work, V1 writes, or result-dependent feature build.

## Process supervision
- Foreground sequential process, no child/background workers. Annual checkpoints are atomic and never silently overwritten.

## Acceptance
- Report `READY_FOR_P2_M01_HISTORICAL_CONTEXT_BUILD` only if a raw-native identity has full enough coverage, no unresolved composite collisions, flat/Ban'ei separation, cutoff isolation, and member provenance.

## Completion record
- Exact raw `馬名 + 生年月日` covered all 1,015,982 retained horselist rows and had zero static profile conflicts inside the flat universe.
- South Kanto target universe: 18,965 horses / 250,093 rows. Other-flat context: 9,290 target horses / 165,475 added rows.
- 79 archives and 158 source members were traced with SHA-256 in foreground sequential processing. Seven annual checkpoints were written.
- `horse_key` is retained as an opaque V1 reference key; its construction is not inferred or extended.
