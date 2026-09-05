# P2 Race-Day Outcome / Exit Contract V1

`./race-day` keeps the shell wrapper as `exec python -m src.operations.race_day`.  The Python application classifies only normal race-day terminal outcomes; argparse and uncaught-Python behavior remain standard.

| Exit class | Code | Meaning |
|---|---:|---|
| `EXPECTED_HEALTHY` | 0 | Expected healthy completion, no meeting, or explicit one-shot wait. |
| `BLOCKED_RECOVERABLE` | 10 | Resume, time, source availability, or operator intervention is needed. |
| `FAILED_INVARIANT` | 20 | Immutable evidence, plan, model/policy, or supervision contract requires investigation. |

At every normal CLI termination, race-day emits exactly one compact block:

```text
RACE_DAY_OUTCOME:
outcome: <EXACT_OUTCOME>
exit_class: <EXPECTED_HEALTHY|BLOCKED_RECOVERABLE|FAILED_INVARIANT>
exit_code: <0|10|20>
scientific_day_complete: <YES|NO>
actual_accounting_complete: <YES|NO>
user_action_required: <YES|NO>
safe_to_resume: <YES|NO>
```

`DAY_COMPLETE_ACCOUNTING_PENDING` is `EXPECTED_HEALTHY`: scientific completion remains true while explicit Main/Experimental purchase confirmation is outstanding.  `DAY_COMPLETE_WITH_BLOCKED_RACES` preserves the scientific day-complete fact but returns 10.  An immutable Recommendation Evidence conflict or Actual Accounting `ERROR` returns 20.

`DAY_COMPLETE_HISTORY_PENDING` preserves `scientific_day_complete=YES` and
completed Actual Accounting while one or more target races remain on the
separate model-history readiness axis.  It is `BLOCKED_RECOVERABLE` / exit 10
with `safe_to_resume=YES`; it must not be rewritten as a clean exit-0 day.

The collector supervisor reads its authoritative `collection_summary.json` after termination.  A nonzero child with valid `COMPLETE_WITH_FAILURES` is `COLLECTOR_COMPLETE_WITH_FAILURES` (10), not a crash.  Missing terminal evidence is `COLLECTOR_CHILD_FAILED` (10); contradictory terminal evidence is an invariant failure (20).  Failed checkpoints remain immutable and are never recaptured by this classification layer.

`POST_RACE_WAITING` remains in-process waiting.  A `--once` wait is exit 0; the established 120-minute ceiling returns `RESULT_WAIT_TIMEOUT` / exit 10.
