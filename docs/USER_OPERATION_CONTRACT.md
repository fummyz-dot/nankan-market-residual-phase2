# User Operation Contract

## Goal
Generate a recommendation early enough for the user to inspect and purchase manually, approximately 10–20 minutes before post.

## Expected race-day input flow
1. Early race day: optional Keibabook training JSON and ability-table JSON.
2. Around T-40: current-info/body-weight URL may be supplied.
3. Around T-20: odds URL may be supplied.
4. Around T-15: if feasible, system recaptures/uses the primary market snapshot, computes features, predictions, and bet candidates, then automatically commits immutable recommendation evidence before showing `ANALYSIS_READY`.
5. User manually purchases.

## Important distinctions
- T-15 is the current engineering candidate only.
- A late scratch may justify a clearly tagged `REVISION_SCRATCH_ONLY`; it must not silently replace the primary prediction for research scoring.
- Missing user URL input is an operational/data miss, not a model `SKIP` decision.

## Manual purchase data
Optional but valuable for later execution-gap analysis:
- whether the user actually purchased;
- approximate purchase odds;
- stake.
These are not required for the model to issue a recommendation.

## Daily operation baseline (not frozen)
1. Morning: place the Keibabook ability and training JSON for all target races once in the daily inbox.
2. Before each target race: execute one race-level command.
3. Upload the emitted `analysis_bundle.json` to ChatGPT once.
4. ChatGPT may present analysis or a future approved bet presentation; purchases remain manual.

The normal `race-shadow` path records its recommendation evidence automatically;
the user does not need a separate freeze command before manual purchase. Legacy
freeze commands remain diagnostic interfaces.
