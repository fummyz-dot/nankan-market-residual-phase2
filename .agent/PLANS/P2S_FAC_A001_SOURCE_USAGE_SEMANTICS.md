# Codex Job Plan

## Job metadata
- Job ID: P2S_FAC_A001_SOURCE_USAGE_SEMANTICS
- Title: Feature source usage adjudication artifact
- Status: COMPLETE
- Owner: Codex

## Objective
Persist the Research Lead's supplied usage-specific V1 source adjudication without modifying the immutable G0 inventory.

## Allowed inputs
- The supplied `FEATURE_AVAILABILITY_CONTRACT_V1 Amendment 001` specification.
- `reference/v1/db` schema read-only evidence.
- G0 amendment artifacts for source-triage provenance only.

## Read-only inputs
- `audit/g0/` and every existing data/reference artifact.

## Allowed modifications
- `data/manifests/feature_source_adjudication_v1.csv`
- `audit/data/p2s_fac_a001_source_usage_semantics/`
- `.agent/PLANS/P2S_FAC_A001_SOURCE_USAGE_SEMANTICS.md`

## Forbidden actions
- Any G0 artifact edit, DB mutation, model training, feature materialization, threshold/model change, network access, collection, or scientific adjudication beyond the supplied authority.

## Tasks
1. Encode supplied source/use decisions by current, lagged, grouping, and diagnostic use.
2. Add the supplied exclusions for market, provenance, same-day, and high-cardinality metadata.
3. Validate decision enum, uniqueness, schema coverage, and explicit `last_seen_date` prohibition.
4. Write a provenance manifest and validation artifact.

## Tests / acceptance criteria
- Every decision is in the supplied enum.
- `races`, `race_runners`, and `horses` source columns are all represented.
- No row turns a current outcome or market source into an allowed V1 model input.
- G0 source inventory remains unmodified.

## Leakage and temporal checks
- Current/race-day variable fields remain blocked; lagged outcomes explicitly require `source_race_date < target_race_date` and same-day exclusion.

## Run manifest requirements
- `vcs_mode: none`, `git_commit: null`, input/config/output hashes, commands, platform, library versions, and `random_seed: null`.

## Completion
- Created `data/manifests/feature_source_adjudication_v1.csv` with 106 usage-specific adjudication rows.
- G0 inventory was not modified; its SHA-256 is recorded in the run manifest.
- Read-only schema/prohibition validation passed for all `races`, `race_runners`, and `horses` columns.
- No feature materialization, DB mutation, collection, training, network access, or scientific decision beyond supplied Amendment 001 occurred.
