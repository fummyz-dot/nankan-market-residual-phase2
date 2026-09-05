# P2-M12B — Online Shadow Pipeline Blocker Report

## STATUS

`BLOCKED_IN_P2_M12B` before model training, inference, prediction freeze, or performance evaluation.

## Reuse audit result

The Phase 2 legacy builder can be reused only with a complete current target roster. The Class, Speed, and Pace implementations are historical builders; they do not expose an approved strict-prior current-target materialization interface. The A02B3 bundle, current snapshot, Market normalization, Keibabook context, and M12A ledger are reusable foundations.

## Blocking source semantic

`P2_HORSE_IDENTITY_V1` requires the exact composite `horse_name + birth_date`. The current official snapshot schema stores horse number, body weight/change, and declared jockey only. The available Keibabook daily payload contributes a horse name/ID, age, pedigree, and entry fields, but no birth date. Neither source therefore provides the required exact live identity tuple.

Resolving a current horse by name alone against `p2_history_context.sqlite`, even when one present-day lookup happens to be unique, would be a new name-only/fuzzy identity path. That is prohibited. It would also make unknown or future collision behavior un-auditable.

## Consequence

Without an exact identity source, V1 historical rolling semantics and the strict-prior Class/Speed/Pace features cannot be safely materialized for the current roster, and an FS04=178 live vector cannot be claimed. Training `DEV-LIVE-V1`, producing a prediction, or adding M12B prediction rows to the ledger would therefore create an unapproved and potentially semantically different model path.

No outcome, payout, performance, ROI, model training, feature search, or M12A ledger mutation occurred in this audit.

## Required resolution

Provide or approve an official pre-race source that carries birth date together with the current horse, or a separately audited immutable official-ID-to-`P2_HORSE_IDENTITY_V1` crosswalk with exact provenance. After that prerequisite, the online target adapters for existing V1/Class/Speed/Pace builders can be implemented and parity-tested without inventing feature semantics.
