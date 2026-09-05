# P2 Official Pedigree Identity Crosswalk Contract

`P2_OFFICIAL_PEDIGREE_IDENTITY_CROSSWALK_V1` is a narrow fallback for an
official detailed pre-race card row whose direct `/uma_info/<id>.do` link is
absent. It does not replace `P2_HORSE_IDENTITY_V1`.

The sole accepted tuple is exact `horse_name_exact + sire + dam + damsire`.
All four values must be present in the current official card and must resolve
to exactly one official-derived record in `p2_history_context.sqlite.horses`.
The recovered canonical key and birth date are retained with
`EXACT_OFFICIAL_PEDIGREE_CROSSWALK` provenance; the birth date is never claimed
to have been displayed on the current card.

Direct card-to-official-detail identity remains priority I1. This fallback is
I2 only. Missing fields, an absent canonical tuple, or more than one canonical
candidate are hard blocks. No whitespace repair, Unicode/fuzzy matching,
name-only matching, mutable trainer/owner/jockey/sex field, result-page source,
or Keibabook source is permitted.
