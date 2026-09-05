# P2 Market Evidence Class Contract

Market observations must retain one evidence class:

- `HISTORICAL_MARKET_TIME_UNKNOWN`: historical development reference only; never evidence of actual T-15 availability or a live Primary parameter.
- `PROSPECTIVE_TIMESTAMPED_STABILIZATION`: direct timestamped collector response usable only for schema, roster, q, and freshness QA; no outcome or performance join.
- `PROSPECTIVE_DEVELOPMENT`: future approved timestamped development evidence.
- `FUTURE_HOLDOUT`: future untouched confirmatory evidence.

Historical and prospective q datasets are physically separate. Historical roster reconciliation is reference-only and never establishes T-15 roster equivalence.
