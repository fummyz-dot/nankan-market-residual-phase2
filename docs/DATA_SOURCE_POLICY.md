# Data Source and Availability Policy

## Source classes
### NAR historical
Used for strict-as-of historical features. Current known fields include finish time, runner last-3F, race laps, race-level corner passage, raw conditions, prize, body weight/result records, jockey/trainer/horse identity.

### Actual pre-race market
No historical collector is confirmed. Phase 2 must build a new prospective snapshot pipeline. Monthly official odds with `MARKET_TIME_UNKNOWN` are development references only.

### Current pre-race URL input
The user may supply a URL around T-40 for body weight/current information. Raw content may contain odds. The parser must be whitelist-based: only approved current fields may enter `P2_CURRENT`.

### Odds URL input
The user may supply an odds URL around T-20. If technically possible, the same source may be recaptured around T-15. T-15 is an engineering candidate, not a frozen decision time yet.

### Keibabook
Training and ability JSON may be supplied early on race day. Store from day one. Treat as external-source data until an approved Phase 2X protocol uses it.

## Availability rule
A feature is usable only when its evidence establishes:

`available_at(feature) <= decision_time`

Unknown publication times must not be invented.

## Raw vs curated separation
- Raw captures are retained with hash and capture timestamp.
- Curated feature tables contain only approved fields.
- Prohibited fields may exist in raw captures but must be blocked from curated features.

## Prohibited current-race fields
Unless explicitly part of the approved market baseline/trajectory namespace:
- odds;
- popularity/rank;
- payout;
- current result;
- final time;
- post-decision snapshots;
- subjective predictions/marks.


### Historical development cutoff isolation
For the currently audited V1 reference corpus, Phase 2 historical-development aggregates must filter `races.race_date <= 2026-07-31` and apply the same race-key filter to `race_runners`. `source_month` is provenance metadata and must not extend that date cutoff. Rows after the cutoff remain excluded even if future raw provenance is recovered. `horses.last_seen_date` is global entity metadata and is prohibited in historical as-of feature construction because it can include post-cutoff observations.
