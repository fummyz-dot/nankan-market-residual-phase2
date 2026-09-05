# Research Governance

## Primary scientific question
Does a frozen candidate improve the calibrated actual pre-race market baseline on an untouched future holdout?

## Evaluation hierarchy
1. Probability signal vs trivial baselines.
2. Incremental edge vs calibrated market.
3. Economic edge under frozen bet-selection/execution assumptions.

## Primary scope
ALL four South Kanto venues. Venue, month, odds band, field size, class, and other segments are diagnostics unless preregistered otherwise.

## Ticket-level independence
WIN, WIDE, TRIO have separate primary candidates, metrics, and statuses. A WIN failure does not invalidate WIDE/TRIO joint-structure hypotheses.

## CORE business scopes
Maintain V1 bands as secondary scopes unless amended before holdout:
- WIN: `8 <= primary_snapshot_odds < 25`
- WIDE: `10 <= primary_snapshot_lower_odds < 20`
- TRIO: `30 <= primary_snapshot_odds < 80`

## Economic operating targets (business inputs, not yet statistical sufficiency)
Provisional targets to be converted into ticket-specific minimum practical effects during development:
- average >= 30 bets/month;
- >= 360 bets/12 months;
- conservative-haircut expected ROI >= +8%;
- realized flat-stake ROI > 0 for economic confirmation;
- ROI uncertainty must be evaluated separately; 360 bets is not automatically sufficient.

## Holdout
Final holdout starts only after model/features/decision time/selection/evaluation are frozen. Proposed ending rule:

`max(12 calendar months, 3000 eligible races, power-analysis-required sample)`

No outcome-dependent shortening or extension.
