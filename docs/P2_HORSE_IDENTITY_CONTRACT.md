# P2 Horse Identity Contract

## Scope
This contract addresses only historical-context completeness for South Kanto target horses. It does not approve `P2_XVENUE` model features, change target venues, or alter losses/evaluation.

## Raw-native hierarchy
1. Use a stable raw-native horse identifier if the corpus supplies and audit confirms one.
2. Otherwise use a deterministic composite only from raw fields whose labels and observed stability are audited.
3. Name-only is audit-only and never a production canonical identity. Fuzzy matching is prohibited.

## Candidate in this corpus
The raw horselist labels `馬名` and `生年月日` are audited as an exact composite candidate. The resulting identity is eligible only if coverage, static-field consistency, one-to-many name behavior, and cross-venue stability pass the P2-M00 audit. No raw-native stable identifier exists to measure renamed/display-name variants: exact matching is therefore conservative and may miss a renamed history, but must never be widened by fuzzy matching. The V1 `horse_key` is an opaque reference identifier and is not extended to raw 14-venue data unless its construction is evidenced.

## Temporal rule
For a target race `r`, a context row is usable only where `history.race_date < r.race_date`. Same-calendar-date history is prohibited unless a separate ordered availability/event-time contract is approved. `horses.last_seen_date` is prohibited from any historical as-of identity or feature construction.

## Boundaries
South Kanto (大井・船橋・川崎・浦和) remains the prediction target. Other flat NAR rows are historical-context candidates only. 帯広ばんえい is excluded. No odds, model score, class crosswalk, speed normalization, or fuzzy reconciliation is part of this contract.
