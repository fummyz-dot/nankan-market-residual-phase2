# P2-M12B-R7 Official Pedigree Crosswalk Recovery

## STATUS

`NONSTARTER_OFFICIAL_IDENTITY_RECOVERED`

The approved fallback retains `P2_HORSE_IDENTITY_V1` unchanged. A direct
official horse-detail route remains first priority; only its absence permits an
exact `horse_name_exact + sire + dam + damsire` lookup in the official-derived
canonical master.

## Evidence and audit

- Canonical master: 43,544 horses; 43,544 complete pedigree tuples; zero
  tuple-collision groups.
- Hidden-direct-ID simulation: 100 runners tested; zero wrong identities.
- Blocked `2026-08-07 浦和2R` nonstarter #5 `オサケノオトモニ` resolves uniquely
  to `P2H_bc28c5aecb4dc98c36add7f2563e2a056bc2c48effd31abe316058e1636b39a0`,
  with canonical birth date `2024-01-28`.

Raw official-card and official-detail provenance, inventory, uniqueness, and
simulation rows are retained in `audit/data/p2_m12b_r7/`. No result source,
Keibabook, performance computation, model search, or ROI path was used.

## Next

R4-A resumed from the preserved Urawa 2R failure and committed that race using
the approved identity route. It subsequently stopped independently at Urawa
6R: its official final table has a `競走中止` runner with no numeric finish
position despite a final field size of ten. The required frozen history-state
semantics for that status have not yet been audited, so later R4/P7 stages do
not continue. The prior R6 semantic block is retained in its own audit record.
