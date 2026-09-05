# P2 Class Empirical Rating Contract

## Scope
`P2_CLASS_EMPIRICAL_MAIN_V1` is a South-Kanto-only (`NANKAN_TARGET`) strict-as-of online pairwise Bradley–Terry rating. It is separate from `P2_CLASS_RULE`: official A1–C3 order remains an institutional context, not a continuous empirical score. Other-flat NAR and Ban'ei results never update this Main rating; `P2_XVENUE` model use remains unapproved.

## Frozen configuration
- Rating family: `online_pairwise_bradley_terry` only.
- Selected configuration: `R3`, `K=1.00`; status `EMPIRICAL_RATING_VALIDATED`.
- Initial score is `0.0`; identity is `P2_HORSE_IDENTITY_V1`. No transfer/other-venue seed, name-only identity, decay, margin weighting, or class weighting is used.
- The complete selection record is `configs/features/P2_CLASS_EMPIRICAL_SELECTED.yaml`.

## Result and timing safety
Only `FINISHED` runners with positive numeric finish positions are pairwise comparable. Ties, `RAW_FINISH_STATUS_MISSING`, cancellations, exclusions, disqualifications, and unknown statuses are not ranked by inference. For each calendar date, all pre-race outputs observe state through the preceding date only; that date's race gradients use frozen pre-race scores and are applied together after all date outputs are locked.

## Update universe
Explicit JRA exchange, local exchange, and bare/unresolved `交流` races are excluded from rating updates. C3, newcomer, age-conditioned, ungraded, special, and South-Kanto-only grade/open races remain rating-update candidates subject to result safety. Draft purchase eligibility is not an update gate.

## Selection and planned M03B fields
The only K grid is `0.25`, `0.50`, `1.00`. Selection is race-equal pairwise log loss for 2021–2024 after 2020 burn-in; ties within `1e-4` select the smaller K. 2025 is validation-only and 2026-01–07 is diagnostic-only. Planned M03B race fields use rated pre-race runners only: `field_rating_mean`, `field_rating_median`, `field_rating_top3_mean` (NULL if <3), `field_rating_dispersion` (NULL if <2), coverage, and the documented context-prior fallback. Cold-start runner and race-strength deltas remain NULL where the defined prior does not exist.

## Context prior
Canonical/mixed hierarchy: exact ruleset+top+bottom, ruleset+top, ruleset global, global historical prior. Special hierarchy: exact ruleset+taxonomy+grade, ruleset+taxonomy, ruleset global, global historical prior. Context observations contain only pre-race ratings and are date-blocked. Historical program points and statistical confidence intervals are not fabricated.

## Prohibited uses
No Market/odds/popularity/payout source participates. Current-race outcomes never enter a feature join. The prototype is an engineering/audit artifact, not an approved model feature set.
