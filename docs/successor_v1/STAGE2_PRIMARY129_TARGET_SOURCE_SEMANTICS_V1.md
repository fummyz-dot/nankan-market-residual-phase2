# Stage2 Primary129 Target Source Semantics V1

Status: **FROZEN BEFORE ANY STAGE2 OUTCOME RECONCILIATION**

## Why this contract exists

JOB007R2 passed clean-room historical parity but blocked before prediction because
three Primary129 target fields had no frozen prospective source rule:

```text
jockey_affiliation
log_prize_1
log_prize_total
```

This contract adds source semantics only. It does not change the trained model,
feature order, market mapping, Stage2 gates, or blinding rule.

## Historical meaning

The historical context was built from the immutable NAR raw fields:

```text
prize_1 = 1着賞金(円)
prize_2 = 2着賞金(円)
prize_3 = 3着賞金(円)
prize_4 = 4着賞金(円)
prize_5 = 5着賞金(円)

jockey_affiliation = 騎手所属
```

Prize unit is integer yen.

The frozen Job003 feature transforms are:

```text
jockey_affiliation =
  strip(raw) if nonempty else "__MISSING__"

log_prize_1 =
  log1p(prize_1) if prize_1 is not null else null

log_prize_total =
  log1p(sum(non-null prize_1..prize_5))
  if at least one prize is non-null
  else null
```

No extra category normalization is allowed.

## T15 prediction source

Use only the exact official current-card raw archive associated with the frozen
T15 current snapshot.

### Jockey affiliation

Bind to the exact active runner row and the exact official jockey
`/kis_info/<id>.do` anchor already used for declared-jockey identity.

The affiliation must be an explicit displayed affiliation token belonging to
that jockey in the same runner row.

Return the affiliation semantic text without presentation parentheses and strip
leading/trailing whitespace only.

Never infer affiliation from:

```text
jockey name
prior race
trainer
venue
result page
post-race page
```

If the source has an explicit empty affiliation, encode `__MISSING__`.

If the parser cannot prove the affiliation token or the row is ambiguous, block.

### Prize schedule

Bind only to an explicit race-level prize schedule on the same official
pre-race card and the same race identity.

Places 1 through 5 must each have a source status:

```text
EXPLICIT_VALUE_YEN
EXPLICIT_NOT_PUBLISHED
```

Accepted displayed units are `円` and `万円`.

`万円` is converted with exact decimal arithmetic times 10,000 and must produce
an integral yen value.

A DOM miss, ambiguous prize section, unitless amount, or unrecognized unit is
not a null. It is a hard source block.

Do not derive prize from class, race name, another race, result pages, payout
tables, or the web.

## Post-settlement EB update source

For the all-South-Kanto future EB state update, use the retained
`OFFICIAL_CARD` raw archive in the live-history delta.

Use the same jockey-affiliation and prize parser.

These fields must come from the card, not from the result raw archive.

The raw fixed-M2 score is computed from pre-race-safe fields first. Outcome
fields may be attached only afterward to form the residual for future dates.

## Initial source audit scope

For the initial locked replay, audit through:

```text
2026-09-03
```

Require full source resolution for:

1. every `T15_STANDARD_ELIGIBLE` race from 2026-08-01 through 2026-09-03;
2. every retained South-Kanto `OFFICIAL_CARD` needed for the post-cutoff EB
   update through 2026-09-03.

No result or payout access is allowed during this source-semantics audit.

If any source cannot meet this contract, fail closed. Do not add a fallback.
