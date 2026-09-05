"""P7 retained-input precheck; emits no prediction freeze and reads no result DB."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from src.operations.live_feature_materializer import materialize_t15_fs04, score_dev_live_v1

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit" / "data" / "p2_m12b"
MODEL = ROOT / "models" / "development" / "dev_live_v1" / "model.txt"


def _atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(*, race_date: str, venue: str, race_number: int) -> dict:
    materialized = materialize_t15_fs04(race_date=race_date, venue=venue, race_number=race_number)
    if materialized["primary_eligibility"]["status"] != "PRIMARY_ELIGIBLE":
        raise RuntimeError(f"P7_PRIMARY_ELIGIBILITY_REQUIRED:{materialized['primary_eligibility']}")
    prediction = score_dev_live_v1(materialized)
    payload = {
        "phase": "P7_LIVE_INFERENCE_PRECHECK",
        "status": "PASS",
        "mode": "RETAINED_INPUT_ENGINEERING_PRECHECK",
        "race": materialized["identity"],
        "primary_eligibility": materialized["primary_eligibility"],
        "t15_timing_status": materialized["t15_snapshot"]["t15_timing_status"],
        "active_roster_rows": len(materialized["rows"]),
        "feature_count": len(materialized["feature_names"]),
        "feature_name_hash": hashlib.sha256("\n".join(materialized["feature_names"]).encode()).hexdigest(),
        "model_sha256": hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        "market_probability_sum": sum(row["market_calibrated_p"] for row in prediction),
        "candidate_probability_sum": sum(row["candidate_probability"] for row in prediction),
        "provider": materialized["provider_counts"],
        "v1_category_unresolved": 0,
        "prediction_artifact_written": False,
        "prediction_frozen": False,
        "result_db_accessed": 0,
        "performance_evaluated": False,
        "roi_evaluated": False,
    }
    _atomic(AUDIT / "P7_LIVE_INFERENCE_PRECHECK_PASS.json", payload)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--race", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(run(race_date=args.date, venue=args.venue, race_number=args.race), ensure_ascii=False, sort_keys=True))
