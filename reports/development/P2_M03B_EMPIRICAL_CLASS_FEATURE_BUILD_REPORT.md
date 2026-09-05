# P2-M03B — Empirical Class Feature Build Report

## 1. STATUS
`READY_FOR_P2_M04_SPEED_FOUNDATION`

## 2. Frozen rating config
Read and validated from the M03A freeze: online pairwise Bradley–Terry, `R3`, `K=1.00`, calendar-date block, other-flat prohibition, and exchange update prohibition.

## 3. Rating rebuild
The engine rebuilt pre-ratings from source state; M03A prototype parity: 250093 rows, 0 mismatches.

## 4. Race strength
21849 races and 250093 runners were emitted. Rated runners exclude cold starts. Zero-rated races: 914.

## 5. Context prior
Only strictly earlier race pre-rating means populated context observations. Fallback distribution: {"INITIAL_GLOBAL_ZERO": 60, "L1_EXACT": 21228, "L2_TAXONOMY": 16, "L2_TOP": 13, "L3_RULESET": 505, "L4_GLOBAL": 27}.

## 6. Runner and previous-race deltas
Runner delta non-null: 230037; race-strength delta non-null: 231128. Prior-race state is strictly earlier calendar date.

## 7. Official class transition
Safe canonical top-step values: 163780; special/noncanonical cases remain NULL rather than coerced.

## 8. Cold start / transfer
Cold-start zero is not added to the observed field mean. Other-flat history is context metadata only and never rating seed/update input.

## 9. RAW_FINISH_STATUS_MISSING
Profiled by year, venue, source month, and finish-position presence. Safe result-status policy was retained. Review required: False.

## 10. Same-day, exchange, and other-flat safety
Same-day rating and previous-race leakage are zero. Exchange races have pre-race rows but zero post-race updates. Other-flat/Ban'ei rating updates are zero.

## 11. Feature contract and determinism
Logical rebuild hashes match: `PASS`. The class ablation registry remains exactly RuleOnly and RulePlusEmpirical.

## 12. Next stage
P2-M04 speed foundation may begin.
