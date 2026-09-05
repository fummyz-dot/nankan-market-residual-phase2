# P2-WIDE-OPS-V0-001 — Exact PL WIDE + Fixed Decision Policy V1

## Job metadata

- Job ID: P2-WIDE-OPS-V0-001
- Status: RUNNING
- Owner: Codex

## Objective

Extend the existing `race-shadow` pre-race path to derive exact
Plackett–Luce WIDE probabilities from unchanged DEV-LIVE-V1 WIN candidate
probabilities and deterministically emit fixed-policy WIN/WIDE recommendations.

## Inputs

- Unchanged `DEV-LIVE-V1` prediction output (`candidate_probability`,
  `market_calibrated_p`) and its exact T15 WIN rows.
- The exact existing `market_snapshot.sqlite` `PRIMARY_CANDIDATE` / T15 WIDE
  capture set, read-only at inference time.
- Exact withdrawn active roster from the W1 card-status path.
- `configs/ops_bet_policy_v1.json` with the user-supplied fixed bytes.

## Outputs

- A deterministic PL/WIDE and policy computation module.
- Additive `wide_ops_v0` and `recommendation` blocks in the existing shadow
  bundle, with a policy-file hash and all ticket evaluations.
- Existing collector persistence of already-supported official WIDE rows for
  the same mark/capture set; no new parser or store.
- Tests and `audit/data/p2_wide_ops_v0_20260824/` artifacts.

## Invariants

- No retraining, feature/probability modification, threshold tuning, result or
  reconciliation DB access, payout access, or automatic purchase.
- Only `candidate_probability`, calibrated WIN market probability, exact T15
  WIN odds, and exact T15 WIDE lower/upper odds enter the policy.
- WIDE failure is isolated: the unchanged WIN evaluation remains available.
- No subset re-normalization; inactive/withdrawn WIDE pairs fail closed.
- All production source accesses are read-only during `race-shadow`.

## State / failure semantics

- Complete WIDE market: `wide_ops_v0.status=READY`, scope `FULL`.
- Missing/invalid WIDE market: WIDE-only status such as
  `WIDE_MARKET_INCOMPLETE`; scope `PARTIAL`; WIN remains evaluated.
- Field size below three: `WIDE_UNAVAILABLE`; scope `PARTIAL`.
- A priced withdrawn pair: `T15_WITHDRAWN_ROSTER_CONFLICT`, no silent pair
  removal or re-normalization.
- Policy threshold ties are inclusive.  The deterministic race cap preserves
  threshold-passing evaluations as `recommended=false` with
  `RACE_TICKET_CAP`.

## Tasks

1. Reuse the existing collector/parser and T15 market query boundary to expose
   one exact WIN+WIDE snapshot set; persist official WIDE rows at capture time.
2. Implement pure exact PL, lower-only WIDE market mass, and fixed policy
   functions with validation/failure isolation.
3. Extend existing `race-shadow` payload/bundle additively and render its
   compact recommendation from the stored recommendation block.
4. Run targeted math, market, withdrawal, policy, bundle regression, and
   fresh-process engineering smoke tests; write manifests and audits.

## Acceptance tests

- Mathematical, market completeness, withdrawn, cap/tie, WIN-isolation,
  unchanged WIN, no-result-access, and fresh-process replay tests enumerated
  in the task instruction.

## Run manifest

- `vcs_mode: none`, `git_commit: null`, source/config/input hashes, platform,
  Python/library versions, null seed, commands, output artifact hashes.
