# P2 Official Runner Affiliation Prefix Contract

`P2_HORSE_IDENTITY_V1 = exact horse_name + birth_date` is unchanged.  This
contract only separates a verified official race-card display annotation from
the comparison name while retaining the raw display value.

The only approved leading tokens are the observed R4 vocabulary in
`configs/features/P2_OFFICIAL_RUNNER_AFFILIATION_PREFIX_V1.yaml`: `[J]` with
official trainer affiliation `JRA`, `[兵]` with `兵庫`, and `[高]` with `高知`.
For each, the raw card name, token, and resulting identity-comparison name are
stored separately.  The token must be exact and leading; any other leading
bracket token blocks. Brackets elsewhere in a horse name are not modified.

Card processing is ordered as follows:

1. card raw display name → exact approved affiliation token extraction → card
   identity-comparison name;
2. official detail raw name → the separate R5 terminal `（抹消）` handling →
   detail identity-comparison name;
3. card and detail comparison names must be exact equal, followed by the
   existing official birth-date identity route.

No arbitrary bracket stripping, fuzzy normalization, name-only join, or
preauthorization of unobserved tokens is permitted.
