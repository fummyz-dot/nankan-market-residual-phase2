"""Atomic closeout artifacts for P2-M12B P7--P11.

This consumes only prior checkpoint artifacts and predecision P7/P8 outputs;
it deliberately does not open any result, payout, or reconciliation database.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit" / "data" / "p2_m12b"
R13 = ROOT / "audit" / "data" / "p2_m12b_r13"
REPORT = ROOT / "reports" / "development" / "P2_M12B_ONLINE_SHADOW_PIPELINE_REPORT.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"M12B_REQUIRED_ARTIFACT_MISSING:{path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    freshness = load(R13 / "R13_LIVE_HISTORY_FRESHNESS_GATE_PASS.json")
    p7 = load(AUDIT / "P7_LIVE_INFERENCE_PRECHECK_PASS.json")
    p10 = load(AUDIT / "P10_HIDDEN_RESULT_E2E_PASS.json")
    p11 = load(AUDIT / "P11_20260820_ENGINEERING_REPLAY_PASS.json")
    prediction_path = ROOT / "outputs/live_shadow_predictions/2026-08-20/川崎_race08_engineering_replay.json"
    bundle_path = ROOT / "outputs/analysis_bundles/2026-08-20/川崎_race08_analysis_bundle.json"
    template_path = ROOT / "outputs/analysis_bundles/2026-08-20/川崎_race08_decision_template.json"
    prediction, bundle, template = load(prediction_path), load(bundle_path), load(template_path)
    if freshness["status"] != "LIVE_HISTORY_FRESHNESS_GATE_PASS" or p7["status"] != "PASS" or p10["status"] != "PASS" or p11["status"] != "PASS":
        raise RuntimeError("M12B_CHECKPOINT_STATUS_FAILED")
    if prediction["feature"]["count"] != 178 or prediction["history"]["same_day_rows_visible"] != 0 or prediction["result_db_accessed"] != 0:
        raise RuntimeError("M12B_P7_CONTRACT_FAILED")
    if bundle["source_boundary"]["result_db_accessed"] != 0 or bundle["source_boundary"]["result_fields_present"] or bundle["source_boundary"]["payout_fields_present"]:
        raise RuntimeError("M12B_P8_RESULT_LEAKAGE")
    if template["decision_status"] != "ANALYSIS_TEMPLATE_ONLY_NOT_FROZEN_NOT_LIVE_ELIGIBLE":
        raise RuntimeError("M12B_P11_TEMPLATE_BOUNDARY_FAILED")
    checkpoints = {
        "P7_LIVE_INFERENCE_COMMAND.complete.json": {"status": "PASS", "retained_t15_precheck": p7, "after_post_live_draft": "BLOCKED_WITHOUT_ARTIFACT", "result_db_accessed": 0},
        "P8_ANALYSIS_BUNDLE.complete.json": {"status": "PASS", "bundle_path": str(bundle_path.relative_to(ROOT)), "bundle_file_sha256": sha(bundle_path), "result_fields_present": False, "payout_fields_present": False},
        "P9_PREDICTION_FREEZE.complete.json": {"status": "PASS", "mode": "LIVE_SHADOW_ONLY", "hidden_fixture_freeze": p10["freeze"], "idempotency": p10["idempotency"], "engineering_replay_frozen": False},
        "P10_HIDDEN_RESULT_E2E.complete.json": {"status": "PASS", **p10},
        "P11_20260820_ENGINEERING_REPLAY.complete.json": {"status": "PASS", **p11},
    }
    for filename, value in checkpoints.items():
        write_json(AUDIT / "checkpoints" / filename, value)
    artifacts = [R13 / "R13_D_JULY_PARITY_PASS.json", R13 / "R13_LIVE_HISTORY_FRESHNESS_GATE_PASS.json", AUDIT / "P7_V1_PERSON_CATEGORY_TEXT_SEMANTICS_RECOVERED.json", AUDIT / "P7_LIVE_INFERENCE_PRECHECK_PASS.json", prediction_path, bundle_path, template_path, AUDIT / "P10_HIDDEN_RESULT_E2E_PASS.json", AUDIT / "P11_20260820_ENGINEERING_REPLAY_PASS.json"]
    manifest = {"job_id": "P2-M12B", "status": "READY_FOR_FIRST_PROSPECTIVE_SHADOW_RACE", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "python_version": sys.version, "platform": platform.platform(), "model_retrained": False, "model_search_executed": False, "performance_evaluated": False, "roi_evaluated": False, "result_db_access_during_inference_or_engineering_replay": 0, "commands": ["python3 -m src.audit.p2_m12b_p7_live_inference_precheck --date 2026-08-20 --venue 川崎 --race 8", "./race-shadow --date 2026-08-20 --venue 川崎 --race 8 --engineering-replay", "python3 -m src.audit.p2_m12b_p10_hidden_result_e2e", "python3 -m src.audit.p2_m12b_p11_engineering_replay"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha(path)} for path in artifacts]}
    write_json(AUDIT / "P2_M12B_FINAL_RUN_MANIFEST.json", manifest)
    report = f"""# P2-M12B — Online Shadow Pipeline Closeout\n\n## STATUS\n\n`READY_FOR_FIRST_PROSPECTIVE_SHADOW_RACE`\n\n## Verified gates\n\n- R13 live-history freshness: 204 races / 2,130 runners through 2026-08-20; July FS04-178 parity is 0 / 44 mismatch with max difference 5.000444502911705e-13.\n- P7 frozen V1 person-category recovery and retained T15 materialization: 13 runner rows, 178 features, strict history through 2026-08-19, same-day rows 0.\n- P8 one-file source-separated bundle: no result or payout field.\n- P9: immutable M12A ledger freeze and idempotency exercised in the hidden fixture; post-event engineering replay is explicitly never frozen.\n- P10: hidden-result fixture lifecycle passed after the pre-post freeze; no performance or ROI was evaluated.\n- P11: 2026-08-20 Kawasaki 8R retained-input engineering replay passed with result access 0.\n\n## Operations\n\nBefore a future race, run `python3 -m src.operations.live_history_update --through <previous-date>`, then use `./race-shadow --date YYYY-MM-DD --venue <venue> --race N`. The command materializes P7 and writes the P8 bundle; P9 freeze is separately allowed only before post.\n\n## Boundaries\n\nNo new model search or retraining occurred. August outcomes were not used for model training. The P11 engineering replay did not open result/reconciliation storage, compare a winner, calculate performance, payout, or ROI. The P10 synthetic hidden result is confined to a temporary ledger after freeze and is lifecycle-only.\n"""
    tmp = REPORT.with_suffix(".tmp"); tmp.write_text(report, encoding="utf-8"); tmp.replace(REPORT)
    print(json.dumps({"status": manifest["status"], "manifest": str((AUDIT / "P2_M12B_FINAL_RUN_MANIFEST.json").relative_to(ROOT)), "report": str(REPORT.relative_to(ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
