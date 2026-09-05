# P2-A01R History Cutoff Provenance Report

## Exit status

`RESOLVED_SAFE_TO_CONTINUE`

## Root cause

The 128 races are coherent late DB imports dated 2026-08-02 through 2026-08-13. Race and runner `source_month` agree, and matching `imports` ledger records exist. However, the matching raw `racelist`/`horselist` files and ZIP members are absent from both the immutable reference raw corpus and the V1-original raw directory. Their source bytes cannot be verified from available inputs.

## Classification

- `SOURCE_PROVENANCE_UNRESOLVED`: 128

## Backward contamination

- Race/date partition: no pre-cutoff race or runner uses a post-cutoff source month; no race-key collision; race/runner source months align.
- Entity metadata: 19,272 pre-cutoff runner rows link to `horses.last_seen_date` after the cutoff. This global metadata is now explicitly prohibited from historical as-of construction.

## Policy

The historical development cutoff remains `2026-07-31`. All 128 races remain excluded from development aggregates regardless of potential later provenance recovery. Build pre-cutoff tables by `races.race_date` and matching `race_key`, never by `source_month` alone.

## Conclusion

The raw provenance gap is retained as an audit issue, but race/runner partitioning is structurally safe when the cutoff policy and `horses.last_seen_date` blacklist are enforced. No model, performance, ROI, or feature-effectiveness work was performed.
