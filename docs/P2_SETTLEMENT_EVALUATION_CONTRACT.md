# P2 Settlement Evaluation V1 Contract

`race-evaluate` is a post-race-only operation.  It reads immutable pre-race
recommendation evidence or an immutable legacy frozen Decision, together with
an `RESULT_OFFICIAL_FINAL` capture and its official WIN/WIDE payout rows.  It
never imports or invokes prediction code, writes an actual purchase, or alters
the model, feature set, policy, capture policy, evidence, or legacy Decision.

Strategy precedence is recommendation evidence, then a legacy frozen Decision,
then `NO_PRE_RACE_RECOMMENDATION`.  If the first two both exist they must have
the same canonical decision/tickets/stakes; otherwise settlement is blocked as
`DUAL_STRATEGY_SOURCE_CONFLICT`.

Official payout display amounts are settled as the task-fixed 100-yen unit.
Only the exact official desktop note `返還：<ascending comma-separated horse
numbers>号馬` is automatically interpreted as refund.  Any other official
return/refund wording blocks with `REFUND_REVIEW_REQUIRED`.  Required WIN or
WIDE payout sections must exist for the recommended ticket types; remaining
rows are not re-normalized when incomplete.

For each immutable source hash tuple, settlement is idempotent.  A later
different official result/payout source blocks as
`OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED`; it is never overwritten.  WIN log
loss uses only the winner's pre-race stored Candidate and calibrated Market
probabilities.  `T15_STANDARD`, `PRE_RACE_FALLBACK`, and unknown legacy timing
remain separately labelled.
