# P2-M07 — Primary Target Universe & Market-Offset Model Foundation

## STATUS
`READY_FOR_P2_M08_MARKET_BASELINE_AND_RESIDUAL_PROTOCOL`

## Frozen universe
All 21,849 Nankan races received exactly one pre-race-only status: {'SECONDARY_ONLY': 5224, 'PRIMARY_ELIGIBLE': 12251, 'PRIMARY_EXCLUDED': 4374}. No result, Market, or performance field was read by race eligibility. Primary eligibility contains 11,566 explicit C2-or-higher and 685 high-level/open races. Exclusions are 3,376 C3-containing, 784 newcomer, and 214 JRA-exchange races. Secondary-only contains 5,111 class-floor-unverifiable, 69 unresolved bare-exchange, and 44 local-exchange-floor-unverifiable races. All 5,906 original draft-review races are resolved: 686 Primary and 5,220 Secondary-only.

## Outcome semantics
All 250,093 runners were retained in a separate outcome dataset. Starter counts are {'STARTER_VALID_FINISH': 244505, 'NONSTARTER': 4667, 'STARTER_NO_VALID_FINISH': 921}. WIN labels are usable for 21668 races and unresolved for 181 races. There are 20 dead-heat races, with unit soft-target mass and a maximum of two winners. The unresolved-label races have no safe official winner/starter label and did not affect race eligibility.

## Model foundation
WIN is the engineering gate. The frozen future probability form is market-offset race softmax with training-fold-only positive gamma and race-equal soft-target multinomial log loss. FS00–FS04 remain unchanged. No Market data was opened and no model was fit. The backend inventory was read-only; no listed optional modeling backend is installed, which is an environment fact rather than a model-family decision.

## Roster limitation
The historical matrix remains `HISTORICAL_DEVELOPMENT_ROSTER`; no T-15 roster equivalence is claimed.
