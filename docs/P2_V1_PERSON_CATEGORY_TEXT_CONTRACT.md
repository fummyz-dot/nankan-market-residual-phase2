# P2 V1 Person Category Text Contract V1

`P2_V1_LEGACY_V1` consumes `jockey` and `trainer` as frozen categorical
features and as frozen historical aggregation keys.  The M01 historical raw
source supplied those strings directly; the V1 builder contains no person-name
shortening or normalization.

For a retained or future official pre-race card, the only approved bridge is
`P2_OFFICIAL_PERSON_CATEGORY_CROSSWALK_V1`:

1. use the official `/kis_info/<id>.do` or `/cho_info/<id>.do` anchor as the
   person identity;
2. preserve the card's registered display and its exact compact-card display;
3. use the compact display as `V1_legacy_token` only after its exact token is
   present in the frozen V1 historical vocabulary.

The raw official runner display, official person ID, registered display, and
V1 compatibility token are separate values in the rebuildable normalized
delta cache.  No name-only identity join, fuzzy match, generic truncation,
generic bracket stripping, generic symbol stripping, or whitespace repair is
allowed.  A legacy text collision remains a legacy category collision; it is
not used as a person identity elsewhere.

For a genuinely unseen official person, the same pre-race card must expose an
exact compact display.  The frozen DEV-LIVE-V1 `FoldSafePreprocessor` maps a
model-unseen category to `__UNKNOWN__` (code `1`); it must never be coerced to
another person/token.  Missing official-ID/compact-display evidence blocks
with `BLOCK_V1_PERSON_CATEGORY_UNRESOLVED`.

The provider uses only the compatibility token for V1 history.  Result and
reconciliation databases are outside this path.
