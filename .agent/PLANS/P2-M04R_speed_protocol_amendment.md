# P2-M04R — Speed Protocol Amendment & Course-Only Baseline Freeze

## Scope

Record the P2-M04A going-adjusted validation failure without altering its
historical artifacts. Promote only the pre-specified `COURSE_ONLY_ALL_HISTORY`
reference as a separately versioned, provisional development standard.

## Inputs

- Read-only `db/p2_history_context.sqlite`
- Immutable P2-M04A selected-config and validation artifacts
- The existing P2-M04A course-only reference implementation

## Invariants and exclusions

- No new speed candidate, parameter, search grid, or re-selection.
- Course-only uses the existing all-history course hierarchy, median location,
  lambda 20, date-block rule, result-status rule, and MAD speed-scale logic.
- Going adjustment is exactly `NONE`; class, Market, other-flat, and Ban'ei
  data are not inputs. Exchange races never update state.
- Outputs are separate from M04A and M04B artifacts. M04A selected S3 remains
  unchanged and is retained as the failed historical record.

## Acceptance

- `P2-AMEND-001` records all already-seen selection, validation, and diagnostic
  information and requires fresh prospective development evidence.
- Course-only rebuild is deterministic and passes same-day/exchange/source
  isolation audits.
- Tests cover artifact preservation, fixed course-only settings, no search,
  and source prohibitions. No background worker is used.

## Completion record

- Completed foreground on 2026-08-19 with `P2-AMEND-001`.
- Reused the pre-specified M04A `COURSE_ONLY_ALL_HISTORY` implementation twice;
  race and runner logical hashes matched exactly.
- M04A's selected S3 artifact SHA-256 matched its own recorded manifest and was
  not rewritten. Status: `READY_FOR_P2_M04B_SPEED_FEATURE_BUILD_AMENDED`.
