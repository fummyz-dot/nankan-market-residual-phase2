# P2 Outcome Semantics Contract

Outcome semantics are physically separate from the M06 feature matrix. `FINISHED` runners with a positive numeric official finish are `STARTER_VALID_FINISH`; audited `競走中止` rows are `STARTER_NO_VALID_FINISH`; audited `出走取消`, `競走除外`, `競走取止め`, and `競走不成立` rows are `NONSTARTER`. Any other raw combination is `UNRESOLVED_OUTCOME_STATUS`.

WIN training labels use `WIN_SOFT_TIE_TARGET_V1`: one winner receives 1.0; a dead heat with k winners gives each winner `1/k`; other valid starters and started/no-finish runners receive 0.0. Nonstarters are excluded from the training denominator. Races without a safe winner/starter label are explicitly `WIN_TRAINING_LABEL_UNRESOLVED` and never silently filtered.

WIDE/TRIO settlement labels are deferred to a future official payout contract. Outcome semantics do not alter pre-race race eligibility.
