# P2-WIDE-EXPERIMENTAL-MANUAL-PURCHASE-CONFIRM-V0-001

## Scope

Add an explicit CLI-only confirmation recorder for an already committed
Experimental `MANUAL_BUY_RECOMMENDED` intent.  It records only a user-declared
manual purchase as separate immutable JSON evidence.  It does not select,
recommend, purchase, settle, or modify `actual_bets`.

## Inputs and output

- Input: one existing immutable intent under
  `outputs/live_development/wide_experimental_v0/intents/YYYY-MM-DD/`.
- Output: one deterministic confirmation JSON under
  `outputs/live_development/wide_experimental_purchase_confirmations/YYYY-MM-DD/`.
- The evidence contains the SHA-256 of the exact input bytes, the copied
  ticket identity, fixed 100-yen stake, and explicit manual-confirmation
  declaration only.

## Invariants

- The command requires `--confirm-purchased`; no race-day path calls it.
- Current allowlist contains only `P2_WIDE_FUNABASHI_EXPERIMENTAL_V0`; schema
  remains venue-neutral for a future separately authorized policy.
- Confirmation accepts only a valid T15 scientific `MANUAL_BUY_RECOMMENDED`
  intent requiring manual purchase with a 100-yen pair ticket.
- Same path + SHA-256 is idempotent.  Changed bytes at a previously confirmed
  intent path, or different confirmation bytes at its deterministic output,
  fail closed.
- If immutable Main Recommendation Evidence is locally available and valid,
  its scheduled post time is an authoritative deadline and confirmation must
  precede it.  If it is absent, evidence records only user-confirmed time.
- No result/payout/evaluation/settlement/actual-bet source is opened.

## Validation

Synthetic tests cover valid evidence, SHA binding, timing, idempotency,
conflict, rejected intents, absent explicit flag, result/actual-bet isolation,
and renderer command.  Run race-day, Funabashi Shadow/Experimental, and
compileall regression checks.
