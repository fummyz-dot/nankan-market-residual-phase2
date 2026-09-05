# P2-CURRENT-JOCKEY-PARSE-V1-001

## Inputs

- Saved official P2_CURRENT raw cards, with 2026-08-24 Funabashi 6R--10R as the primary regression corpus.
- Existing official card parser, roster-status parser, body-weight parser, current snapshot storage, and bundle path.

## Outputs

- `declared_jockey_raw` read only from an explicit official jockey anchor/source, or `null` with `CURRENT_JOCKEY_UNRESOLVED`.
- Required audit artifacts in `audit/data/p2_current_jockey_parse_v1_20260826/`.

## Invariants and exclusions

- No pedigree/adjacent-cell/name-dictionary/Keibabook fallback.
- No changes to FS04, DEV-LIVE-V1, Policy V2, WIDE research, identity, body-weight, or withdrawal semantics.
- Result/outcome access is zero; production databases are read-only for this task.
- Active roster must stay 11 for 2026-08-24 Funabashi 6R with #3 withdrawn.

## Plan

1. Audit saved raw DOMs and the parser/storage consumers; record the explicit official jockey source contract.
2. Replace positional extraction with the audited explicit source and preserve unresolved values as `null` plus an auditable warning.
3. Add focused parser and regression tests, then run fresh-process fixture and top-level engineering smoke on temporary DBs.
4. Write required audit artifacts and a provenance run manifest.
