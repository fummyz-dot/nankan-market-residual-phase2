# P2-OPS-BET-POLICY-V2-001 — Main WIN-only policy

## Job metadata

- Status: COMPLETE
- Scope: add immutable Policy V2, make it the new-day default, and retain
  Policy V1 as the authority for existing day plans and evidence.

## Inputs and outputs

- Inputs: immutable `ops_bet_policy_v1.json`, the specified V2 policy bytes,
  existing recommendation/evidence/day-plan contracts, and the separate
  prospective WIDE research shadow.
- Outputs: a two-version policy resolver, V2 main recommendation metadata,
  plan-aware policy selection, targeted tests, and a V2 audit bundle.

## Invariants and exclusions

- V1 bytes, V1 evidence, existing manifests, DEV-LIVE-V1, WIN thresholds,
  WIDE research models, and the WIDE research shadow are unchanged.
- A new plan freezes V2.  A pre-existing plan resolves its stored ID and exact
  hash and never silently migrates to the current default.
- V2 evaluates/recommends/stakes WIN tickets only; WIDE diagnostics and the
  independent research shadow remain available and never affect Main scope or
  stake.
- No result/outcome/actual-bet access, historical V2 backfill, policy tuning,
  or production DB mutation.

## State and acceptance

1. Resolve policy from registry with exact file SHA-256 and create a V2 plan
   only when no manifest exists.
2. Route the plan's frozen policy to race-shadow and validate the same policy
   in Recommendation Evidence.
3. Main V2 output is `FULL`, has enabled WIN/disabled WIDE metadata, and
   cannot recommend/stake WIDE; V1 output remains unchanged when explicitly
   selected.
4. Verify new/restart V2, V1 resume, WIDE-only edge, WIN invariance,
   research isolation, and fresh-process temporary-DB smoke.

## Completion

- V2 policy SHA-256 and both versioned policy paths are frozen in the audit
  manifest. New day plans use V2; retained V1 plans resolve their stored hash
  and remain byte-identical on resume.
- Targeted unit/integration/fresh-process smoke passed with no production DB
  access or mutation. The separate prospective WIDE research bundle was
  verified without changing its source, model artifacts, or Main policy path.
