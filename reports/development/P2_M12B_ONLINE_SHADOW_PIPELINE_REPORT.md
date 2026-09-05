# P2-M12B — Online Shadow Pipeline Closeout

## STATUS

`READY_FOR_FIRST_PROSPECTIVE_SHADOW_RACE`

## Verified gates

- R13 live-history freshness: 204 races / 2,130 runners through 2026-08-20; July FS04-178 parity is 0 / 44 mismatch with max difference 5.000444502911705e-13.
- P7 frozen V1 person-category recovery and retained T15 materialization: 13 runner rows, 178 features, strict history through 2026-08-19, same-day rows 0.
- P8 one-file source-separated bundle: no result or payout field.
- P9: immutable M12A ledger freeze and idempotency exercised in the hidden fixture; post-event engineering replay is explicitly never frozen.
- P10: hidden-result fixture lifecycle passed after the pre-post freeze; no performance or ROI was evaluated.
- P11: 2026-08-20 Kawasaki 8R retained-input engineering replay passed with result access 0.

## Operations

Before a future race, run `python3 -m src.operations.live_history_update --through <previous-date>`, then use `./race-shadow --date YYYY-MM-DD --venue <venue> --race N`. The command materializes P7 and writes the P8 bundle; P9 freeze is separately allowed only before post.

## Boundaries

No new model search or retraining occurred. August outcomes were not used for model training. The P11 engineering replay did not open result/reconciliation storage, compare a winner, calculate performance, payout, or ROI. The P10 synthetic hidden result is confined to a temporary ledger after freeze and is lifecycle-only.
