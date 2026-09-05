# P2 Current Official Identity Source Contract

The canonical identity remains `P2_HORSE_IDENTITY_V1 = exact horse_name +
birth_date`. The official horse ID is provenance only and does not replace the
composite identity.

Source priority is fixed:

1. I1: an official `/uma_info/<official-id>.do` link present in that same
   saved pre-race card, followed by the linked official horse-detail page and
   its full official birth date;
2. I2: when I1 is absent, the exact static official detailed-card tuple
   `horse_name_exact + sire + dam + damsire`, resolving to exactly one
   official-derived canonical `P2_HORSE_IDENTITY_V1` master record under
   `P2_OFFICIAL_PEDIGREE_IDENTITY_CROSSWALK_V1`;
3. I3: block.

Before I1/I2 comparison, a saved official card display name may pass through
the separately frozen `P2_OFFICIAL_RUNNER_AFFILIATION_PREFIX_V1` exact leading
prefix rule. Raw display remains preserved; only its approved comparison name
is used. The card's `YY.M.D` date is retained raw and never expanded by a heuristic. It
is usable only after exact validation against I2's `YYYY年M月D日` detail value.
Card and detail horse names must be exact equal. I2 does not change the
canonical key or claim that a current card displayed the recovered birth date;
it is a complete-tuple crosswalk to exactly one existing canonical record.
Missing tuple fields, zero candidates, or multiple candidates block. Name-only,
fuzzy, Keibabook, result-page, and manual mapping are prohibited.
