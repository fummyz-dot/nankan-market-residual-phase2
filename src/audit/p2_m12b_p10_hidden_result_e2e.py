"""P10 hidden-result lifecycle test using P7/P8 artifacts and the M12A ledger.

The only outcome used here is an in-memory synthetic fixture, introduced after
the pre-post freeze.  It never evaluates accuracy, payout, return, or ROI.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters.nankan_official import FetchResult
from src.operations.freeze_live_shadow_prediction import freeze_live_shadow
from src.operations.live_dev_reconcile import reconcile
from src.operations.live_development_store import connect
from src.operations.official_result_collector import persist_final_result

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12b"
PREDICTION = ROOT / "outputs" / "live_shadow_predictions" / "2026-08-20" / "川崎_race08_engineering_replay.json"
BUNDLE = ROOT / "outputs" / "analysis_bundles" / "2026-08-20" / "川崎_race08_analysis_bundle.json"


def _atomic(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def run() -> dict:
    """Execute one complete hidden-result fixture lifecycle, isolated in tmp."""
    if not PREDICTION.is_file() or not BUNDLE.is_file():
        raise RuntimeError("P10_RETAINED_P7_P8_ARTIFACT_MISSING")
    source_prediction = json.loads(PREDICTION.read_text(encoding="utf-8"))
    source_bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="p2_m12b_p10_") as tmp:
        root = Path(tmp)
        bundle = copy.deepcopy(source_bundle)
        bundle["mode"] = "LIVE_SHADOW"
        bundle["prediction_info"]["freeze_status"] = "P9_REQUIRED_NOT_WRITTEN"
        bundle_path = root / "hidden_predecision_bundle.json"
        _atomic(bundle_path, bundle)
        prediction = copy.deepcopy(source_prediction)
        prediction["mode"] = "LIVE_SHADOW_DRAFT"
        prediction["prediction_freeze"] = "P9_REQUIRED_NOT_WRITTEN"
        prediction["analysis_bundle"] = {"path": str(bundle_path), "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest()}
        prediction_path = root / "hidden_predecision_prediction.json"
        _atomic(prediction_path, prediction)
        ledger = root / "live_development.sqlite"
        frozen = freeze_live_shadow(prediction_path=prediction_path, db_path=ledger, frozen_at=prediction["timing"]["scheduled_post_time"].replace("09:30:00", "09:20:00"))
        idempotent = freeze_live_shadow(prediction_path=prediction_path, db_path=ledger, frozen_at=prediction["timing"]["scheduled_post_time"].replace("09:30:00", "09:20:00"))
        race = {"race_key": prediction["race"]["race_key"], "race_date": prediction["race"]["race_date"], "venue": prediction["race"]["venue"], "race_number": prediction["race"]["race_number"], "scheduled_post_time": prediction["timing"]["scheduled_post_time"], "source_entry_url": "synthetic://hidden-result-fixture"}
        parsed = {"finality_status": "RESULT_OFFICIAL_FINAL", "runners": [{"horse_number": 1, "finish_position": 1, "result_status": "STARTER_VALID_FINISH", "raw_status": "HIDDEN", "parse_status": "SYNTHETIC_HIDDEN_RESULT"}], "payouts": [{"ticket_type": "WIN", "combination_raw": "1", "payout_raw": "HIDDEN", "payout_amount": 100, "payout_unit": None, "parse_status": "SYNTHETIC_HIDDEN_RESULT"}, {"ticket_type": "WIDE", "combination_raw": "1-2", "payout_raw": "HIDDEN", "payout_amount": 100, "payout_unit": None, "parse_status": "SYNTHETIC_HIDDEN_RESULT"}, {"ticket_type": "TRIO", "combination_raw": "1-2-3", "payout_raw": "HIDDEN", "payout_amount": 100, "payout_unit": None, "parse_status": "SYNTHETIC_HIDDEN_RESULT"}]}
        # Isolate fixture raw provenance from normal operational archives.
        import src.operations.live_development_store as store
        original_root = store.RAW_ROOT
        original_workspace_root = store.ROOT
        store.RAW_ROOT = root / "hidden_result_raw"
        store.ROOT = root
        try:
            persisted = persist_final_result(db_path=ledger, race=race, fetch=FetchResult("synthetic://hidden-result", "2026-08-20T09:31:00+00:00", "2026-08-20T09:31:01+00:00", "synthetic://hidden-result", [], 200, {"Content-Type": "text/html"}, b"P10 hidden result fixture"), parsed=parsed)
        finally:
            store.RAW_ROOT = original_root
            store.ROOT = original_workspace_root
        reconciliation = reconcile(race["race_date"], db_path=ledger)
        con = connect(ledger)
        try:
            frozen_rows = con.execute("SELECT COUNT(*) FROM decision_records WHERE state='FROZEN'").fetchone()[0]
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            con.close()
    if frozen["status"] != "FROZEN" or idempotent["status"] != "IDEMPOTENT_NOOP" or persisted != "RESULT_OFFICIAL_FINAL" or reconciliation[0]["status"] != "RECONCILED" or frozen_rows != 1 or fk:
        raise RuntimeError("P10_HIDDEN_RESULT_E2E_FAILED")
    return {"status": "PASS", "pre_race_result_access": 0, "feature_count": 178, "freeze": frozen["status"], "idempotency": idempotent["status"], "synthetic_result_path": persisted, "reconciliation": reconciliation[0]["status"], "performance_evaluated": False, "roi_evaluated": False, "m12a_reused": True}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result = run() | {"executed_at": datetime.now(timezone.utc).isoformat(), "vcs_mode": "none", "git_commit": None}
    target = OUT / "P10_HIDDEN_RESULT_E2E_PASS.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
