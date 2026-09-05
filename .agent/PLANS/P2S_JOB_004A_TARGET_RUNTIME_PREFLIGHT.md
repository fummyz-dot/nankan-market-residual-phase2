# Job004A plan

- Status: COMPLETE_BLOCKED_JOB003_STARTER_SEMANTICS
- Inputs: Job003 canonical datasets; Evaluation Freeze V1 and Amendment 001.
- Scope: audited starter/effective-rank checks, immutable Job003 starter-semantics audit, and no-network runtime inventory only.
- Exclusions: all fitting, PL/EB/bootstrap/evaluation, market/payout/ROI, network, and Job003 modification.

## Result
- Effective-rank and Top3 starter integrity pass for all 21,560 races; no unresolved status appears in the accepted universe.
- Job003 composition violates Amendment 001 actual-starter rule: `20200127_KAWASAKI_11`, `comp_ability_mean`, stored `-0.2926124651659148` vs actual-starter recomputation `-0.5174930409817028` (absolute difference `0.22488057581578796`).
- Runtime also lacks CatBoost and pandas. No package installation, model fit, or canonical-artifact mutation occurred.
