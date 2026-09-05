# P2 Live Development Ledger Contract

`db/live_development.sqlite` is the isolated append-only ledger for live
development decisions, official results, payouts, and reconciliation. It is
not a feature store or Market/current-info database.

Decision records have `DRAFT`, `FROZEN`, or `VOIDED_BEFORE_POST` state. A
freeze is permitted only when `frozen_at < scheduled_post_time`; timestamps
are offset-aware, stored in UTC, and displayed in JST. Frozen decision inputs,
runner predictions, tickets, and snapshot references are immutable.

The only engineering exception is an explicitly labelled synthetic fixture.
It cannot be used as a real-race decision or evaluation record.

Every write uses `PRAGMA foreign_keys=ON` and an explicit transaction. A failed
child insert rolls back the capture; failed work is recorded only as a failure
event, never as a successful result artifact.

`P2_RECOMMENDATION_EVIDENCE_V1` is a separate immutable pre-race operational
evidence layer. It records a `race-shadow` Policy V1 recommendation and its
predecision provenance without recording a user purchase or replacing legacy
Decision freezes. Result collection remains naturally-keyed and does not
require recommendation evidence. See
`docs/P2_RECOMMENDATION_EVIDENCE_CONTRACT.md`.
