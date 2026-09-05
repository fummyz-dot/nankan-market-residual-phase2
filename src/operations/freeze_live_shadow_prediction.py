"""P9 transaction-safe freeze of an already-created LIVE_SHADOW draft.

Model materialisation lives in P7.  This module intentionally only verifies
the immutable P7/P8 artifacts and delegates persistence to the frozen M12A
ledger function.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from src.operations.live_dev_freeze_decision import freeze_decision
from src.operations.live_development_store import DEFAULT_DB, connect, initialize_database

ROOT = Path(__file__).resolve().parents[2]
PREDICTION_ROOT = ROOT / "outputs" / "live_shadow_predictions"


class LiveShadowFreezeError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(prediction: dict[str, Any], bundle: Path) -> dict[str, Any]:
    if prediction.get("mode") != "LIVE_SHADOW_DRAFT":
        raise LiveShadowFreezeError("P9_LIVE_SHADOW_DRAFT_REQUIRED")
    if prediction.get("result_db_accessed") != 0 or prediction.get("performance_evaluated") or prediction.get("roi_evaluated"):
        raise LiveShadowFreezeError("P9_PREDECISION_RESULT_BOUNDARY_FAILED")
    if prediction.get("feature", {}).get("count") != 178:
        raise LiveShadowFreezeError("P9_FEATURE_COUNT_MISMATCH")
    if not bundle.is_file() or _sha(bundle) != prediction.get("analysis_bundle", {}).get("sha256"):
        raise LiveShadowFreezeError("P9_BUNDLE_HASH_MISMATCH")
    parsed_bundle = json.loads(bundle.read_text(encoding="utf-8"))
    if parsed_bundle.get("mode") != "LIVE_SHADOW" or parsed_bundle.get("source_boundary", {}).get("result_db_accessed") != 0:
        raise LiveShadowFreezeError("P9_BUNDLE_SOURCE_BOUNDARY_FAILED")
    race = prediction["race"]
    market_rows = prediction["predictions"]
    if not market_rows or "snapshot_id" not in prediction.get("timing", {}):
        # The P7 payload has intentionally only one compact timing object;
        # take the exact market row reference recorded in the one-file bundle.
        market_snapshot_id = parsed_bundle["timing_provenance"].get("market_snapshot_id")
    else:
        market_snapshot_id = prediction["timing"]["snapshot_id"]
    if not market_snapshot_id:
        raise LiveShadowFreezeError("P9_MARKET_SNAPSHOT_REFERENCE_MISSING")
    current_snapshot_id = parsed_bundle["timing_provenance"].get("current_snapshot_id")
    if not current_snapshot_id:
        raise LiveShadowFreezeError("P9_CURRENT_SNAPSHOT_REFERENCE_MISSING")
    runners = []
    for row in sorted(market_rows, key=lambda item: int(item["horse_number"])):
        runners.append({"horse_number": int(row["horse_number"]), "model_probability": float(row["candidate_probability"]), "market_probability": float(row["market_calibrated_p"]), "edge": float(row["edge_log_ratio"]), "rank": 1 + sum(float(other["candidate_probability"]) > float(row["candidate_probability"]) for other in market_rows)})
    return {
        "schema_version": "P2_LIVE_DECISION_V1",
        "race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "race_number": int(race["race_number"]),
        "scheduled_post_time": prediction["timing"]["scheduled_post_time"],
        "decision_created_at": parsed_bundle["timing_provenance"]["current_captured_at"],
        "market_snapshot_id": market_snapshot_id, "current_snapshot_id": current_snapshot_id,
        "analysis_bundle_path": str(bundle), "analysis_bundle_sha256": _sha(bundle),
        "model_version": prediction["model"]["version"], "feature_set": "FS04_178", "model_artifact_sha256": prediction["model"]["model_sha256"],
        "decision_status": "SHADOW_ONLY", "runner_predictions": runners, "recommended_tickets": [],
    }


def freeze_live_shadow(*, prediction_path: Path, db_path: Path = DEFAULT_DB, market_db: Path | None = None, frozen_at: str | None = None) -> dict[str, Any]:
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    bundle_ref = prediction.get("analysis_bundle", {}).get("path")
    if not bundle_ref:
        raise LiveShadowFreezeError("P9_BUNDLE_REFERENCE_MISSING")
    bundle = ROOT / bundle_ref
    payload = _payload(prediction, bundle)
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    initialize_database(db_path)
    con = connect(db_path)
    try:
        existing = con.execute("SELECT decision_id,decision_input_sha256 FROM decision_records WHERE race_key=? AND state='FROZEN'", (payload["race_key"],)).fetchone()
    finally:
        con.close()
    if existing:
        if existing["decision_input_sha256"] == digest:
            return {"status": "IDEMPOTENT_NOOP", "decision_id": existing["decision_id"], "race_key": payload["race_key"]}
        raise LiveShadowFreezeError("P9_FROZEN_PREDICTION_CONFLICT")
    kwargs: dict[str, Any] = {"db_path": db_path}
    if market_db is not None:
        kwargs["market_db"] = market_db
    if frozen_at is not None:
        kwargs["frozen_at"] = frozen_at
    decision_id = freeze_decision(payload, **kwargs)
    return {"status": "FROZEN", "decision_id": decision_id, "race_key": payload["race_key"], "input_sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze one pre-post LIVE_SHADOW draft; no inference is run.")
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(json.dumps(freeze_live_shadow(prediction_path=args.prediction, db_path=args.db), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
