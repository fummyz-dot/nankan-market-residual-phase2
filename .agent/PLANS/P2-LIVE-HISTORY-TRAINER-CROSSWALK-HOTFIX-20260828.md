# P2-LIVE-HISTORY-TRAINER-CROSSWALK-HOTFIX-20260828

## Scope

Resolve only the 2026-08-26 船橋8R trainer `040442` live-history
normalization block. Frozen DEV-LIVE-V1, FS04, policy, and category
vocabularies remain unchanged.

## Inputs and finding to verify

- Raw delta official card: race id `2026082619060308`, card capture
  `d7b845def3a13335f332523413b515ad6ea439284ccd98ff72b93c327329ce13`.
- Official card evidence: horse 13 has trainer anchor `/cho_info/040442.do`,
  registered/compact display `田中勝`.
- Frozen base and DEV-LIVE category map contain neither `田中勝` nor raw result
  display `田中勝春`.
- Existing frozen preprocessor maps an unseen categorical token to code `1`
  (`__UNKNOWN__`).

## Minimum change

Permit a unique official-ID/card-backed person token absent from the frozen
base vocabulary to remain a resolved official context and reach the existing
frozen unknown-category transform. Preserve blocking for absent/ambiguous card
identity, nonunique registered/token evidence, and all runner-card join errors.
Do not add a base token or map a token to a different legacy category.

## Tests

1. Existing full raw-delta build covers `040442` as a resolved official unseen
   trainer and records category code `1`.
2. Known in-base trainer remains `SEEN` with the same token.
3. Missing/ambiguous official crosswalk evidence remains blocked.
4. Jockey behavior remains unchanged; FS04 count/model hash/policy hash remain
   frozen.
5. Run a fresh Python process against copied raw/normalized fixture paths;
   then run the bounded production history-refresh command only after the
   fixture passes.

## Exclusions

No retraining, category vocabulary change, new module/class/table/CLI/config,
or production result/reconciliation access.
