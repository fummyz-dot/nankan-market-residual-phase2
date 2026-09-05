"""P11 retained-input 2026-08-20 engineering replay closeout.

It reads only the P7/P8 predecision artifacts.  No result database/table,
official result page, payout, winner, or performance metric is opened.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREDICTION = ROOT / "outputs" / "live_shadow_predictions" / "2026-08-20" / "川崎_race08_engineering_replay.json"
BUNDLE = ROOT / "outputs" / "analysis_bundles" / "2026-08-20" / "川崎_race08_analysis_bundle.json"
OUT = ROOT / "audit" / "data" / "p2_m12b"
TEMPLATE = ROOT / "outputs" / "analysis_bundles" / "2026-08-20" / "川崎_race08_decision_template.json"


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run() -> dict:
    if not PREDICTION.is_file() or not BUNDLE.is_file():
        raise RuntimeError("P11_P7_OR_P8_ARTIFACT_MISSING")
    prediction = json.loads(PREDICTION.read_text(encoding="utf-8"))
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    if prediction.get("mode") != "POST_EVENT_ENGINEERING_REPLAY" or bundle.get("mode") != "POST_EVENT_ENGINEERING_REPLAY":
        raise RuntimeError("P11_ENGINEERING_MODE_REQUIRED")
    if prediction.get("result_db_accessed") != 0 or bundle.get("source_boundary", {}).get("result_db_accessed") != 0:
        raise RuntimeError("P11_RESULT_ACCESS_PROHIBITED")
    if prediction.get("feature", {}).get("count") != 178 or len(prediction.get("predictions", [])) != prediction.get("active_roster_count"):
        raise RuntimeError("P11_FS04_OR_ROSTER_CONTRACT_FAILED")
    if prediction["history"].get("same_day_rows_visible") != 0 or prediction["history"].get("max_history_date") > "2026-08-19":
        raise RuntimeError("P11_STRICT_ASOF_FAILED")
    if prediction.get("prediction_freeze") != "P9_REQUIRED_NOT_WRITTEN" or bundle.get("prediction_info", {}).get("freeze_status") != "POST_EVENT_ENGINEERING_REPLAY_NOT_LIVE_ELIGIBLE":
        raise RuntimeError("P11_FREEZE_BOUNDARY_FAILED")
    template = {
        "schema_version": "p2_post_event_engineering_decision_template_v1",
        "mode": "POST_EVENT_ENGINEERING_REPLAY",
        "race": prediction["race"],
        "analysis_bundle_path": str(BUNDLE.relative_to(ROOT)),
        "analysis_bundle_file_sha256": hashlib.sha256(BUNDLE.read_bytes()).hexdigest(),
        "prediction_path": str(PREDICTION.relative_to(ROOT)),
        "prediction_file_sha256": hashlib.sha256(PREDICTION.read_bytes()).hexdigest(),
        "decision_status": "ANALYSIS_TEMPLATE_ONLY_NOT_FROZEN_NOT_LIVE_ELIGIBLE",
        "ticket_selection": None,
        "result_db_accessed": 0,
        "performance_evaluated": False,
        "roi_evaluated": False,
    }
    _atomic(TEMPLATE, template)
    return {"status": "PASS", "race": "2026-08-20 川崎8R", "feature_count": 178, "analysis_bundle": str(BUNDLE.relative_to(ROOT)), "decision_template": str(TEMPLATE.relative_to(ROOT)), "result_access": 0, "performance_evaluated": False, "roi_evaluated": False, "prediction_frozen": False, "mode": "POST_EVENT_ENGINEERING_REPLAY"}


def main() -> None:
    result = run() | {"executed_at": datetime.now(timezone.utc).isoformat(), "vcs_mode": "none", "git_commit": None}
    target = OUT / "P11_20260820_ENGINEERING_REPLAY_PASS.json"
    _atomic(target, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
