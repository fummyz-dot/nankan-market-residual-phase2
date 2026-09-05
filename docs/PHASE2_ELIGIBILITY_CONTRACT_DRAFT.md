# Phase 2 Eligibility Contract — DRAFT

## Status
This is the current operational baseline only. It is not a final-holdout eligibility freeze.

## Explicit exclusion candidates
- `新馬`, `debut`, or `newcomer` → `EXCLUDE_NEWCOMER`
- `JRA交流` / explicit central-JRA exchange → `EXCLUDE_JRA_EXCHANGE`
- a safely parsed class lower than C2 (C3 and below) → `EXCLUDE_BELOW_C2`

## Ambiguity
If race type or class cannot be safely identified, return `REVIEW_REQUIRED` with `AMBIGUOUS_RACE_TYPE` or `AMBIGUOUS_CLASS`. Do not infer inclusion. Local exchange is not treated as JRA exchange without explicit evidence.

## Current example
`Ｃ２(三)(四)` is parsed as C2 and is `ELIGIBLE` under this draft baseline.
