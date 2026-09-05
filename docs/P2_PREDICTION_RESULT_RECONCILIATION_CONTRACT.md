# P2 Prediction / Result Reconciliation Contract

Reconciliation is deterministic from the ledger: an official final result and
a decision `FROZEN` strictly before post time are both required before a race
can be evaluation-eligible. A result cannot make a later-created decision
eligible.

States are `NO_PRE_RACE_DECISION`, `INELIGIBLE_LATE_DECISION`, `RESULT_PENDING`,
`READY_TO_RECONCILE`, `RECONCILED`, and `ERROR`. Actual user bets remain
separate from recommended tickets, so realised P/L is not model performance.

The 2026-08-20 Kawasaki 6R–11R smoke-test races have official results but no
pre-race frozen model decision. They are permanently `NO_PRE_RACE_DECISION` and
are not model-evaluation observations.
