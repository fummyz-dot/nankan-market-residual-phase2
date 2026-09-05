# P2-M12A — Live Development Ledger / Official Result / Reconciliation

## Inputs

- Official URLs already registered by the prospective collector for 2026-08-20 Kawasaki 6R–11R.
- Official result-page links explicitly present in those pages.

## Outputs

- Isolated `db/live_development.sqlite`, decision freeze CLI, official result collector, reconciliation and compact status CLIs.

## Invariants

- Result/payout never enter `market_snapshot.sqlite`, feature store, or current-info tables.
- FK-on explicit transaction; no partial promotion or fake parents.
- Only `FROZEN` decision before scheduled post can ever reconcile as evaluation eligible.
- Today 6R–11R have no decision and must reconcile as `NO_PRE_RACE_DECISION`.
- No model inference, scoring, performance, ROI, or profit calculation.

## Acceptance

- Failure injection, idempotency, finality, duplicate, dead-heat, and isolation tests pass.
- Official 6R–11R raw result captures reconcile idempotently with 100% provenance.
