# P2 Result Completeness State Contract V1

Official post-race availability is recorded on three independent axes.  These
states are provenance/readiness facts; they do not change prediction, sample
membership, settlement formulas, or the strict-as-of history boundary.

## Axis A — result source

`RESULT_WAITING` means no exact-race official result source has begun usable
staged publication.  It is normal temporal waiting and has no fabricated raw
SHA.

`RESULT_PARTIAL` is immutable evidence bound to an exact official raw SHA: the
page identity and result rows are available, while the existing all-of
WIN/WIDE/TRIO final source predicate is incomplete.

`RESULT_OFFICIAL_FINAL` retains its existing meaning: the official collector
has exact identity, result rows, and parseable canonical WIN, WIDE, and TRIO
payout content.  It is not a statement that model history has been promoted.

## Axis B — model-history readiness

`MODEL_HISTORY_WAITING` means the retained source has not yet passed the
strict reusable history-result parser.  `RESULT_MODEL_HISTORY_COMPLETE` means
that parser passed for the exact raw and the race is ready for a later normal
history-promotion attempt.  `MODEL_HISTORY_REVIEW_REQUIRED` is reserved for
explicit identity or outcome-semantic contradictions.

Readiness is not promotion.  Target-day rows are never inserted during
race-day POST.  Promotion remains:

`next eligible prepare → live_history_update(through=target_date - 1) → normalized delta → provider`.

That path retains its own card, pedigree, normalization, and freshness checks.

## Axis C — payout readiness

WIN, WIDE, and TRIO each have `PAYOUT_WAITING`, `PAYOUT_READY`, or
`PAYOUT_REVIEW_REQUIRED`.  The latter is used for an unrecognized official
refund note; the current official note parser is race-wide and therefore marks
all ticket families review-required rather than guessing an affected family.

Payout readiness is observability only.  WIN/WIDE settlement continues to
require the existing `RESULT_OFFICIAL_FINAL` source authority.  Main TRIO
settlement remains unsupported.

## Evidence, resume, and race-day

`result_completeness_evidence` records one append-only assessment per
`race_key + raw_sha256`.  The logical payload is SHA-bound; a conflicting
assessment for the same raw fails closed.  A newer raw before finality creates
a new row.  A different raw after accepted finality is
`OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED`, never a silent supersession.

During POST, `RESULT_WAITING` and `RESULT_PARTIAL` remain
`POST_RACE_WAITING`; partial output prints model-history and each payout axis.
Per-race `RACE_RESULT_MODEL_HISTORY_COMPLETE` and day-level
`RESULT_MODEL_HISTORY_COMPLETE` events mean readiness only.  The latter is
emitted only when every Primary target is history-ready.

If existing settlement reaches scientific `DAY_COMPLETE` while a final source
is still history-waiting, the CLI outcome is
`DAY_COMPLETE_HISTORY_PENDING` / exit 10.  Explicit integrity contradictions
remain exit 20 under the Race-Day Outcome/Exit Contract.
