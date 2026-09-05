# P2-CURRENT-PROSPECTIVE-RESEARCH-V1-001 Plan

## Inputs

- Frozen Main Recommendation Evidence and its immutable `predecision_reference`.
- Existing `market_snapshot.sqlite` `current_info_snapshots` / `current_runner_info` and linked official raw capture.
- Existing normalized historical history, strictly `race_date < target_date` only.
- Existing official P7 person crosswalk only when it proves an exact official identity; no fuzzy/name-only fallback.

## Outputs

- Frozen protocol bundle under `models/development/current_prospective_v1/`.
- Immutable `current_research_evidence` ledger rows in the live-development research DB.
- Per-race payloads and deterministic cumulative coverage-only materialization under `outputs/live_development/current_prospective_v1/`.
- Audit artifacts under `audit/data/p2_current_prospective_v1_20260826/`.

## Invariants / exclusions

- The Main bundle's exact adopted `current_snapshot_id` / `current_capture_id` is authority; no later snapshot replacement.
- The research path reads no result, payout, settlement, `actual_bets`, Policy, or model input path.
- Current jockey IDs are read only from a unique same-row `/kis_info/<id>.do` anchor in the retained official raw.  Missing/ambiguous stays null.
- Previous jockey identity is accepted only when an existing approved exact crosswalk proves it; otherwise `UNKNOWN`, never a display-name comparison.
- All history queries have `race_date < target_date`; same-day / future history is prohibited.
- The evidence ledger is immutable and idempotent by `(race_key, research_bundle_sha256)`.
- After post time no new research evidence is created; a durable `CURRENT_RESEARCH_MISSED` marker records an eligible missed opportunity.

## State / transaction behavior

1. Verify frozen protocol hashes.
2. Load existing Main Evidence read-only; validate T15/fallback and pre-post reference.
3. Read exactly the Main-adopted CURRENT snapshot from the prospective DB read-only, validate Main active roster against CURRENT active runner rows, and build payload.
4. Atomically write payload file then insert one immutable research record in `BEGIN IMMEDIATE`; exact existing payload is an idempotent noop, a different payload conflicts.
5. On restart reuse existing evidence.  Before post, retry only from the immutable Main reference; after post write/read the missed marker only.
6. race-day starts the sidecar only after Main Evidence commit and never awaits it; failures remain research-only.

## Acceptance tests

- Normal T15 and fallback, valid/missing/invalid body weight, official-anchor jockey/no-pedigree fallback.
- Strict prior history and SAME/CHANGED/UNKNOWN/NO_PRIOR statuses.
- Withdrawal, roster conflict, restart reuse, post-race missed/no backfill.
- Main/WIN/WIDE/trajectory coexistence; pre-race result access zero; fresh-process race-day smoke using temporary DBs.
