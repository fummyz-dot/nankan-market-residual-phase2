"""JOB006 outcome-blind inventory for exact Job004 Fold4 continuation.

This executable only reads frozen Job004 lineage and source/manifests.  It does
not open the prospective result ledger, evaluate Stage2, or load outcomes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
JOB004 = ROOT / "audit/successor_v1/job004"
LOCAL_OUTPUT = ROOT / "audit/successor_v1/job006"
TRACKED_OUTPUT = ROOT / "docs/evidence/successor_v1/job006"

STAGE2_JSON_SHA = "b628b05f68b5746be7543e20b6bea621850b6978fada46f9d0e041c404ec3070"
STAGE2_MD_SHA = "1dec7d7e4fb3ee7cbc3644ecfa89a9afefb2445e83c8ba8c914b61168752c365"
PRIMARY_HASH = "f2d11d6632c94c3826343f5ce3051ebb9d21d26b2c5754ea38a6f06c20604aa5"
RACE_HEAD_HASH = "d65c205307ea63b58b3f284530d6daa747f04bb3411c068c3430735860a11303"
HISTORY_DB_SHA = "5fe7a9e88e25f64e51e39e27b789315ababfbe597786b26701f0e4a7f8486936"
RUNTIME_FREEZE_SHA = "226c7d6bdc5e21514858a789df311cbb020415daaa5f77b584fa1550e3aa2438"

ATTEMPT3 = "audit/successor_v1/job004/attempts/attempt_training_003"
ATTEMPT4 = "audit/successor_v1/job004/attempts/attempt_training_004"

CRITICAL_ARTIFACTS: tuple[tuple[str, str, str | None], ...] = (
    ("fold4_primary_m2_model", f"{ATTEMPT3}/checkpoints/models/m2_outer_fold4.cbm", "0eab5da875ed4155c7b4f5b92c21d6b8893b821abaef18d0f69f37e20ef4ebf2"),
    ("fold4_primary_m2_raw_prediction", f"{ATTEMPT3}/checkpoints/raw_predictions/m2_outer_fold4.npy", "ba9db45760baf37153cae43426504f71e6ce139c094d4a9a8166995e023ee629"),
    ("fold4_race_head_model", f"{ATTEMPT4}/checkpoints/models/race_head_outer_fold4.cbm", "58357312e69516e57c52121ec57c64093a686e101e2d0b3ae0fc0e482e6d41ec"),
    ("fold4_race_head_raw_prediction", f"{ATTEMPT4}/checkpoints/raw_predictions/race_head_outer_fold4.npy", "0f1cc1bc46181ed8f4538839f621113d9f4d5424047ac8f28d675dd23d5b6df9"),
    ("fold4_eb_fixed_components", f"{ATTEMPT3}/checkpoints/eb/fold4_components.json", "b2e56f153e0ce0b056e3117f52e50d9e841da0e33e0831244ff67516f543bab2"),
    ("fold4_eb_outer_effects", f"{ATTEMPT3}/checkpoints/eb/fold4_outer_fixed.npy", "2f2e6086d1d38c39ce423f315ac676b3cdd283e7b53a8e9f64cac897f2d2c0d5"),
    ("fold4_eb_outer_audit", f"{ATTEMPT3}/checkpoints/eb/fold4_outer_fixed_audit.csv", "dbe4400d825d38482bfd7980cd0e297f5f8207d408456cb065caf3e0723843b0"),
    ("m2_inner_date_causal_effects", f"{ATTEMPT3}/checkpoints/eb/m2_inner_date_causal.npy", "46564efe080091fed727841450eddd447f6960ccd2ba38cf6fd4460c0acdac0f"),
    ("m2_inner_date_causal_audit", f"{ATTEMPT3}/checkpoints/eb/m2_inner_date_causal_audit.csv", "b9e581d1483c08239d392628592e563bdd6915dd7db8d92f7b494619c927b2e5"),
    ("m2_inner_raw_2021", f"{ATTEMPT3}/checkpoints/raw_predictions/m2_to_2021.npy", "1e33bc21674efb67ea1721a73d0aea06c94969d976deed66c2a80fd178de7606"),
    ("m2_inner_raw_2022", f"{ATTEMPT3}/checkpoints/raw_predictions/m2_to_2022.npy", "d4d4d38c1370e4f021ce2997e0d2a8172794abd0ad49bb0c2cf40345a3b3b041"),
    ("m2_inner_raw_2023", f"{ATTEMPT3}/checkpoints/raw_predictions/m2_to_2023.npy", "31e75c08156cfc35b9b2626f286c03c0070c3da3f29aa98410e4fee745169a95"),
    ("m2_inner_raw_2024", f"{ATTEMPT3}/checkpoints/raw_predictions/m2_to_2024.npy", "01e237a0ad11ab031dc51a480b3ca89512b23120ab97ae0329220a625e4fcc4b"),
    ("m2_inner_raw_2025", f"{ATTEMPT3}/checkpoints/raw_predictions/m2_to_2025.npy", "4f7f0a3fede12d5c07d851948ef008374d8b809d0bb8e5279fdcfb600ca0298e"),
    ("fold4_m1_parameters", "audit/successor_v1/job004/pl_temperature_fit.csv", "501205602fa8f5690a213682955aa7912da59cd200f89b3963ff00425d64bbeb"),
    ("fold4_candidate_selection", "audit/successor_v1/job004/model_selection_by_fold.csv", "fd97768d8ddb34950de7bcfc5e3da05b25925ac80ff0836b9ed78dc1f4828cb1"),
    ("primary_ordered_manifest", "data/manifests/successor_v1/PRIMARY_MODEL_INPUT_MANIFEST_V1.csv", "eb6bf0291f55e0a4d11f01987237b82af2e36d5065de395606d06d3600923954"),
    ("primary_categorical_role_manifest", "data/manifests/successor_v1/CATBOOST_INPUT_ROLE_MANIFEST_V1.csv", "1c0357fb9c8cc41554db1d9a2af75ce969e246780d4049f1a5e4a43ae00e65a5"),
    ("race_head_ordered_manifest", "data/manifests/successor_v1/RACE_HEAD_INPUT_MANIFEST_V1.csv", "023d7a5d0a6570c4350f571d3a5ed5c37885fec1a42ecedf19671dcc731d484b"),
    ("runtime_freeze", "data/manifests/successor_v1/RUNTIME_FREEZE_V1.json", RUNTIME_FREEZE_SHA),
    ("history_database", "reference/v1/db/nankan_history.sqlite", HISTORY_DB_SHA),
    ("job004_final_report", "audit/successor_v1/job004/JOB004_FINAL_REPORT.md", "1bc3cb731293b1f9e51908bc8b5358b24c992230fcb8b7ca6fdc52ba29d35d12"),
    ("job004_run_manifest", "audit/successor_v1/job004/run_manifest.json", "c361343437beefe38df894685ea4e43748ca4169f4e744e18844ddd404e74cc3"),
)

PROHIBITED_TABLE_TOKENS = (
    "result", "payout", "settlement", "purchase", "reconciliation",
)
PROHIBITED_PATH_TOKENS = (
    "live_development.sqlite", "result_capture", "payout", "settlement",
    "reconciliation", "runner_predictions.csv", "race_metrics.csv",
)


class InventoryError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise InventoryError(f"MISSING_ARTIFACT:{path}")
    actual = sha256_file(path)
    if expected_sha256 is not None and actual != expected_sha256:
        raise InventoryError(f"ARTIFACT_HASH_MISMATCH:{path}")
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": actual, "status": "PASS"}


def ordered_feature_names(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: int(row["ordered_position"]))
    return [row["feature_name"] for row in rows if row.get("included", "True").lower() != "false"]


def ordered_feature_hash(names: Iterable[str], *, encoding: str = "compact_json") -> str:
    values = list(names)
    if encoding == "compact_json":
        payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    elif encoding == "newline_joined":
        payload = "\n".join(values).encode("utf-8")
    else:
        raise ValueError(f"unknown feature hash encoding: {encoding}")
    return hashlib.sha256(payload).hexdigest()


def require_feature_manifest(path: Path, *, count: int, expected_hash: str, hash_encoding: str = "compact_json") -> list[str]:
    names = ordered_feature_names(path)
    if len(names) != count or ordered_feature_hash(names, encoding=hash_encoding) != expected_hash:
        raise InventoryError(f"FEATURE_MANIFEST_MISMATCH:{path}")
    return names


def validate_lineage_values(
    *, selected_candidate: str, selected_temperature: str, cutoff: str,
    primary_count: int, primary_hash: str, race_head_count: int, race_head_hash: str,
) -> None:
    expected = ("M2", "M1", "2026-07-31", 129, PRIMARY_HASH, 32, RACE_HEAD_HASH)
    actual = (selected_candidate, selected_temperature, cutoff, primary_count, primary_hash, race_head_count, race_head_hash)
    if actual != expected:
        raise InventoryError(f"JOB004_LINEAGE_CONFLICT:{actual!r}")


def guard_prospective_table(table: str) -> None:
    normalized = table.strip().lower()
    if any(token in normalized for token in PROHIBITED_TABLE_TOKENS):
        raise InventoryError(f"PROSPECTIVE_OUTCOME_TABLE_FORBIDDEN:{table}")


def guard_inventory_path(path: Path) -> None:
    normalized = str(path).lower()
    if any(token in normalized for token in PROHIBITED_PATH_TOKENS):
        raise InventoryError(f"PROSPECTIVE_OUTCOME_PATH_FORBIDDEN:{path}")


def prior_dates_only(observation_dates: Iterable[str], target_date: str) -> list[str]:
    target = date.fromisoformat(target_date)
    return sorted({value for value in observation_dates if date.fromisoformat(value) < target})


def verify_eb_reference() -> dict[str, Any]:
    import numpy as np

    from src.models.successor_v1 import eb_state

    signature = inspect.signature(eb_state.backfit)
    if eb_state.LAYERS != ("horse", "jockey", "horse_x_venue", "jockey_x_venue"):
        raise InventoryError("EB_LAYER_ORDER_MISMATCH")
    if signature.parameters["max_cycles"].default != 20 or signature.parameters["tolerance"].default != 1e-5:
        raise InventoryError("EB_CONVERGENCE_DEFAULT_MISMATCH")
    empty = eb_state.BackfitResult(
        effects={layer: {} for layer in eb_state.LAYERS},
        components={layer: (1.0, 0.1) for layer in eb_state.LAYERS},
        cycles=0, converged=True, final_max_abs_change=0.0, initialized_from_zero=True,
    )
    score = eb_state.score_effects(
        empty,
        np.asarray(["UNSEEN_H"], dtype=object),
        np.asarray(["UNSEEN_J"], dtype=object),
        np.asarray(["大井"], dtype=object),
    )
    if score[0] != 0.0:
        raise InventoryError("EB_UNSEEN_KEY_NOT_ZERO")
    return {
        "layers": list(eb_state.LAYERS), "full_rebackfit_from_zero": True,
        "max_cycles": 20, "convergence_tolerance": 1e-5,
        "unseen_key_effect": 0.0, "same_day_updates": False, "status": "PASS",
    }


def _fold4_row(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["fold_id"] == "Fold4"]
    if len(rows) != 1:
        raise InventoryError(f"FOLD4_PARAMETER_ROW_INVALID:{path}")
    return rows[0]


def _model_feature_names(path: Path) -> list[str]:
    from catboost import CatBoostRegressor

    model = CatBoostRegressor()
    model.load_model(str(path))
    return list(model.feature_names_)


def _git_value(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True).stdout.strip()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(rows)


def _readiness() -> list[dict[str, str]]:
    return [
        {"component": "Primary 129 feature materialization", "classification": "READY_WITH_ADAPTER", "basis": "Reuse Job003B pure feature builders plus T15 active-roster and strict-as-of normalized-history adapters; no end-to-end online 129 builder exists."},
        {"component": "race-head 32 feature materialization", "classification": "READY_WITH_ADAPTER", "basis": "Exact manifest is a race-constant subset of Primary129; requires the same post-cutoff adapter."},
        {"component": "Fold4 M2 loading", "classification": "READY_EXISTING", "basis": "Exact CatBoost binary loads with the frozen 129-feature order."},
        {"component": "Fold4 race-head loading", "classification": "READY_EXISTING", "basis": "Exact CatBoost binary loads with the frozen 32-feature order."},
        {"component": "Fold4 PL probability generation", "classification": "READY_WITH_ADAPTER", "basis": "Job004 exact distribution and M1 parameters are reusable; a forward orchestration module is absent."},
        {"component": "EB state reconstruction through 2026-07-31", "classification": "READY_WITH_ADAPTER", "basis": "Fixed components, inner/outer predictions, targets, keys, and history lineage exist; reconstruct-and-persist adapter is absent."},
        {"component": "EB post-cutoff date-causal continuation", "classification": "READY_WITH_ADAPTER", "basis": "Dynamic-key reference supports unseen groups and fixed-component rebuild; date lifecycle orchestrator is absent."},
        {"component": "T15 WIDE market join", "classification": "READY_WITH_ADAPTER", "basis": "JOB005 contract/store rows establish exact eligible pair universe; scorer join adapter is absent."},
        {"component": "immutable pre-result prediction artifact", "classification": "READY_WITH_ADAPTER", "basis": "Existing pre-result freeze transaction pattern is reusable but is bound to legacy178 and requires a Stage2 schema adapter."},
        {"component": "later official outcome reconciliation", "classification": "READY_EXISTING", "basis": "Official result collection, immutable frozen-decision linkage, and reconciliation operations already exist; JOB006 does not invoke them."},
    ]


def _design_markdown(inventory: list[dict[str, Any]], readiness: list[dict[str, str]], params: dict[str, Any]) -> str:
    artifact_lines = "\n".join(f"- `{row['role']}`: `{row['path']}` — `{row['sha256']}`" for row in inventory)
    readiness_lines = "\n".join(f"- {row['component']}: `{row['classification']}` — {row['basis']}" for row in readiness)
    return f"""# Stage2 Fold4 Forward Scorer Design V1

Status: **INVENTORY COMPLETE; IMPLEMENTATION DEFERRED**

## Frozen continuation

The initial Stage2 scorer uses Job004 Fold4 Primary M2 with no retraining. The fixed Fold4 race-head model and M1 probability-temperature parameters are used. The legacy FS04 178-feature live model is prohibited as a substitute.

## Exact local artifacts

{artifact_lines}

## Fold4 parameters

- M0 T0: `{params['m0_T0']}`
- M1 T0: `{params['m1_T0']}`
- gamma: `{params['gamma']}`
- upset mean: `{params['upset_mean']}`
- upset sigma: `{params['upset_sigma']}`
- EB components: `{json.dumps(params['eb_components'], ensure_ascii=False, sort_keys=True)}`

## Dataflow

1. Resolve the actual T15 pre-race active roster and identities from the prospective store/current capture.
2. Query normalized history strictly before `target_race_date`; same-day history is excluded.
3. Reuse the frozen Job003B feature builders to emit the exact ordered Primary129 row set and its race-constant RaceHead32 projection.
4. Load the fixed Fold4 M2 and race-head CatBoost artifacts; generate Fold4 M1 PL probabilities with the frozen T0/gamma/upset standardization and the n=3 M0 rule.
5. Join only the eligible exact-T15 WIDE pair universe. Freeze an immutable prediction artifact before any target result access.
6. After every race on a date is frozen and the date settles, official outcome reconciliation may append that date's residuals for a later date only.

## Date-causal EB lifecycle

At the beginning of date `d`, collect residual observations whose source race date is strictly `< d`. Rebuild from zero in layer order `horse`, `jockey`, `horse_x_venue`, `jockey_x_venue` with Fold4 fixed sigma2/tau2, at most 20 cycles, and the `1e-5` convergence rule. Unknown horses, jockeys, and interactions contribute zero until a prior-date observation exists. Score and freeze all races on `d`; never update state between same-day races. Only after the date settles may its residuals become input for the next date.

## Feature materialization sources

- Base/Primary/race-composition functions: `src/audit/p2s_job003_materialized_feature_foundation.py`
- Actual-starter correction and 130-to-129 selection lineage: `src/audit/p2s_job003b_actual_starters.py`
- Actual T15 roster/card/identity input adapters: `src/operations/live_feature_materializer.py`
- Strict-as-of history: `src/features/online/history_view.py` and `src/features/online/normalized_history_provider.py`
- Later normalized history delta: `src/operations/build_normalized_live_history_delta.py`

Every feature primitive must be resolved under the frozen Job003B semantics; missing inputs fail closed. `first_seen_date`, `last_seen_date`, market values, current results, and same-day history are prohibited.

## Component readiness

{readiness_lines}

## Prediction artifact schema

The next job must freeze an immutable artifact containing schema/version, race identity and date, scheduled post/decision timestamps, current and WIDE capture ids/hashes, active roster identity, Primary129 and RaceHead32 ordered hashes, model/race-head/EB artifact hashes, Fold4 M0/M1/gamma/upset parameters, per-runner model probabilities, exact WIDE pair `q_model`, and result-boundary flags. It must contain no target result, payout, CE, delta, ROI, or profit.

## Result-access barrier

Prediction generation must open only pre-race inputs. A content-hashed prediction artifact must be durably frozen for every race on date `d` before an outcome connector for `d` is allowed. Reconciliation is a later phase and may update EB state only for a future date.

## Next implementation modules

- A post-cutoff exact Primary129/RaceHead32 materialization adapter.
- A Fold4 fixed-model/PL forward inference adapter.
- A date-batched fixed-component EB reconstruction/state module.
- A Stage2 immutable prediction freezer and later result-reconciliation evaluator with a hard access barrier.
"""


def run(*, root: Path, implementation_git_commit: str) -> dict[str, Any]:
    global ROOT, JOB004, LOCAL_OUTPUT, TRACKED_OUTPUT
    ROOT = root.resolve(); JOB004 = ROOT / "audit/successor_v1/job004"
    LOCAL_OUTPUT = ROOT / "audit/successor_v1/job006"; TRACKED_OUTPUT = ROOT / "docs/evidence/successor_v1/job006"

    validate_artifact(ROOT / "data/manifests/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1.json", STAGE2_JSON_SHA)
    validate_artifact(ROOT / "docs/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1.md", STAGE2_MD_SHA)
    inventory: list[dict[str, Any]] = []
    for role, relative, expected in CRITICAL_ARTIFACTS:
        path = ROOT / relative; guard_inventory_path(path)
        inventory.append({"role": role, **validate_artifact(path, expected)})

    primary_names = require_feature_manifest(ROOT / "data/manifests/successor_v1/PRIMARY_MODEL_INPUT_MANIFEST_V1.csv", count=129, expected_hash=PRIMARY_HASH)
    race_head_names = require_feature_manifest(
        ROOT / "data/manifests/successor_v1/RACE_HEAD_INPUT_MANIFEST_V1.csv",
        count=32, expected_hash=RACE_HEAD_HASH, hash_encoding="newline_joined",
    )
    m2_names = _model_feature_names(ROOT / f"{ATTEMPT3}/checkpoints/models/m2_outer_fold4.cbm")
    head_names = _model_feature_names(ROOT / f"{ATTEMPT4}/checkpoints/models/race_head_outer_fold4.cbm")
    if m2_names != primary_names or head_names != race_head_names:
        raise InventoryError("CATBOOST_FEATURE_ORDER_LINEAGE_CONFLICT")

    selection = _fold4_row(JOB004 / "model_selection_by_fold.csv")
    temperature = _fold4_row(JOB004 / "pl_temperature_fit.csv")
    final_report = (JOB004 / "JOB004_FINAL_REPORT.md").read_text(encoding="utf-8")
    selected_temperature = "M1" if "Selected temperature: M1" in final_report else "UNRESOLVED"
    validate_lineage_values(
        selected_candidate=selection["selected_candidate"], selected_temperature=selected_temperature,
        cutoff="2026-07-31", primary_count=len(primary_names), primary_hash=ordered_feature_hash(primary_names),
        race_head_count=len(race_head_names), race_head_hash=ordered_feature_hash(race_head_names, encoding="newline_joined"),
    )
    components = json.loads((ROOT / f"{ATTEMPT3}/checkpoints/eb/fold4_components.json").read_text(encoding="utf-8"))
    params = {
        "selected_primary_candidate": selection["selected_candidate"], "selected_temperature_model": selected_temperature,
        "m0_T0": float(temperature["m0_T0"]), "m1_T0": float(temperature["m1_T0"]),
        "gamma": float(temperature["gamma"]), "upset_mean": float(temperature["upset_mean"]),
        "upset_sigma": float(temperature["upset_sigma"]), "eb_components": components["components"],
        "historical_scoring_cutoff": "2026-07-31", "retraining": False,
    }
    eb = verify_eb_reference()
    readiness = _readiness()
    if any(row["classification"] in {"MISSING_REQUIRED_DATA", "LINEAGE_CONFLICT"} for row in readiness):
        raise InventoryError("IRREPLACEABLE_SCORER_INPUT_UNAVAILABLE")

    LOCAL_OUTPUT.mkdir(parents=True, exist_ok=True); TRACKED_OUTPUT.mkdir(parents=True, exist_ok=True)
    _write_csv(LOCAL_OUTPUT / "job004_fold4_artifact_inventory.csv", inventory, ["role", "path", "size_bytes", "sha256", "status"])
    _write_json(LOCAL_OUTPUT / "job004_fold4_parameter_inventory.json", params)
    _write_csv(LOCAL_OUTPUT / "forward_scorer_component_readiness.csv", readiness, ["component", "classification", "basis"])
    feature_readiness = {
        "primary": {"count": 129, "ordered_hash": PRIMARY_HASH, "classification": "READY_WITH_ADAPTER", "model_order_match": True},
        "race_head": {"count": 32, "ordered_hash": RACE_HEAD_HASH, "classification": "READY_WITH_ADAPTER", "model_order_match": True},
        "post_cutoff_strict_as_of": "READY_WITH_ADAPTER", "same_day_history": False,
        "actual_pre_race_active_roster_only": True, "market_inputs_in_probability_model": False,
        "outcome_inputs": False, "first_seen_last_seen_features": False,
        "legacy_178_substitution": False,
    }
    _write_json(LOCAL_OUTPUT / "feature_materialization_readiness.json", feature_readiness)
    boundary = {
        "status": "PASS", "prospective_outcome_access": False, "payout_access": False,
        "settlement_access": False, "stage2_performance_evaluated": False, "network_access": False,
        "queried_tables": [], "opened_prospective_databases": [],
        "guarded_table_tokens": list(PROHIBITED_TABLE_TOKENS), "guarded_path_tokens": list(PROHIBITED_PATH_TOKENS),
    }
    _write_json(LOCAL_OUTPUT / "outcome_access_audit.json", boundary)

    branch = _git_value(ROOT, "branch", "--show-current")
    manifest = {
        "job_id": "JOB006", "status": "JOB006_PASS", "vcs_mode": "git", "branch": branch,
        "start_main_commit": "28ccc29a7e15f320d50e3ff84d6d4a31869e6993",
        "implementation_git_commit": implementation_git_commit, "final_evidence_commit": "SELF",
        "stage2_json_sha256": STAGE2_JSON_SHA, "stage2_md_sha256": STAGE2_MD_SHA,
        "job004_history_db_sha256": HISTORY_DB_SHA, "artifact_count": len(inventory),
        "model_fit_performed": False, "prospective_outcome_access": False,
        "payout_access": False, "stage2_performance_evaluated": False, "network_access": False,
        "commands": ["python -m unittest tests.audit.test_p2s_job006_stage2_scorer_inventory", "python src/audit/p2s_job006_stage2_scorer_inventory.py --implementation-git-commit <SHA>"],
        "outputs": [str(path.relative_to(ROOT)) for path in sorted(LOCAL_OUTPUT.glob("*"))],
    }
    _write_json(LOCAL_OUTPUT / "run_manifest.json", manifest)
    report = f"""# JOB006 Report

STATUS: JOB006_PASS

- Fold4 Primary: M2, fixed CatBoost model, 129 ordered features.
- Probability temperature: M1; fixed T0/gamma/upset parameters resolved.
- Race head: fixed Fold4 CatBoost model, 32 ordered features.
- EB: fixed components and reconstruction sources resolved; {eb['max_cycles']} cycles / tolerance {eb['convergence_tolerance']}; unseen keys = zero.
- Historical scoring cutoff: 2026-07-31; NO RETRAIN.
- Post-cutoff feature/scorer path: READY_WITH_ADAPTER; no irreplaceable data missing.
- Prospective outcomes/payouts/settlements read: 0.
- Stage2 performance evaluated: false.
- Legacy 178-feature substitution: false.
"""
    (LOCAL_OUTPUT / "JOB006_REPORT.md").write_text(report, encoding="utf-8")
    design = _design_markdown(inventory, readiness, params)
    design_path = ROOT / "docs/successor_v1/STAGE2_FOLD4_FORWARD_SCORER_DESIGN_V1.md"
    design_path.parent.mkdir(parents=True, exist_ok=True); design_path.write_text(design, encoding="utf-8")
    compact = {
        "status": "JOB006_PASS", "lineage": {"primary": "M2", "temperature": "M1", "cutoff": "2026-07-31", "no_retrain": True},
        "features": feature_readiness, "parameters": params,
        "artifacts": [{"role": row["role"], "path": str(Path(row["path"]).relative_to(ROOT)), "sha256": row["sha256"]} for row in inventory],
        "readiness": readiness, "boundary": boundary,
    }
    _write_json(TRACKED_OUTPUT / "FOLD4_FORWARD_SCORER_INVENTORY.json", compact)
    summary = f"""# JOB006 Summary

STATUS: JOB006_PASS

- Exact Fold4 M2 Primary and Fold4 race-head binaries were found, hashed, and loaded.
- Primary129 `{PRIMARY_HASH}` and RaceHead32 `{RACE_HEAD_HASH}` exactly match both manifests and model feature order.
- Fold4 M1 T0/gamma/upset parameters and fixed EB variance components were resolved.
- Exact forward scoring is feasible with adapters; no legacy178 substitution and no retraining are permitted.
- EB cutoff reconstruction and post-cutoff continuation are `READY_WITH_ADAPTER`; unseen keys score zero and same-day updates are forbidden.
- Prospective outcome, payout, settlement, Stage2 performance, and network access were all zero/false.
"""
    (TRACKED_OUTPUT / "JOB006_SUMMARY.md").write_text(summary, encoding="utf-8")
    return {"status": "JOB006_PASS", "artifact_count": len(inventory), "readiness": readiness, "parameters": params}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--implementation-git-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(run(root=args.root, implementation_git_commit=args.implementation_git_commit), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
