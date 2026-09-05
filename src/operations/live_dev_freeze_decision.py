"""Freeze a pre-race development decision; this module never runs inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.prospective_store import DEFAULT_DB as MARKET_DB
from src.operations.live_development_store import DEFAULT_DB, connect, event, initialize_database, register_race, transaction, utc_iso


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_reference(value: str) -> bool:
    return value.startswith("ENGINEERING_FIXTURE:")


def _verify_references(payload: dict[str, Any], market_db: Path) -> None:
    if payload.get("engineering_fixture") is True:
        required = ("market_snapshot_id", "current_snapshot_id", "analysis_bundle_path", "analysis_bundle_sha256", "model_artifact_sha256")
        if not all(_fixture_reference(str(payload.get(name, ""))) for name in required):
            raise ValueError("engineering fixture references must be explicit")
        return
    bundle = Path(payload["analysis_bundle_path"])
    if not bundle.is_file() or _sha256_file(bundle) != payload["analysis_bundle_sha256"]:
        raise ValueError("analysis bundle reference/hash unavailable")
    conn = sqlite3.connect(f"file:{market_db}?mode=ro", uri=True)
    try:
        market = conn.execute("SELECT 1 FROM market_snapshots WHERE snapshot_id=?", (payload["market_snapshot_id"],)).fetchone()
        current = conn.execute("SELECT 1 FROM current_info_snapshots WHERE current_snapshot_id=?", (payload["current_snapshot_id"],)).fetchone()
    finally:
        conn.close()
    if not market or not current:
        raise ValueError("referenced market/current snapshot does not exist")


def freeze_decision(payload: dict[str, Any], *, db_path: Path = DEFAULT_DB, market_db: Path = MARKET_DB, frozen_at: str | None = None) -> str:
    required = {"schema_version", "race_key", "race_date", "venue", "race_number", "scheduled_post_time", "decision_created_at", "market_snapshot_id", "current_snapshot_id", "analysis_bundle_path", "analysis_bundle_sha256", "model_version", "feature_set", "model_artifact_sha256", "decision_status", "runner_predictions", "recommended_tickets"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing decision input keys: {missing}")
    if payload["decision_status"] not in {"BET", "NO_BET", "SHADOW_ONLY"}:
        raise ValueError("invalid decision_status")
    frozen = utc_iso(frozen_at or datetime.now(timezone.utc))
    post = utc_iso(payload["scheduled_post_time"])
    if frozen >= post:
        raise ValueError("DECISION_AFTER_POST_REJECTED")
    _verify_references(payload, market_db)
    input_digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    horses: set[int] = set()
    for row in payload["runner_predictions"]:
        horse = int(row["horse_number"])
        if horse in horses or not (0 < float(row["model_probability"]) <= 1 and 0 < float(row["market_probability"]) <= 1):
            raise ValueError("invalid or duplicate runner prediction")
        horses.add(horse)
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        with transaction(conn):
            race = {key: payload[key] for key in ("race_key", "race_date", "venue", "race_number", "scheduled_post_time")}
            race["source_entry_url"] = None
            register_race(conn, race)
            existing = conn.execute("SELECT 1 FROM decision_records WHERE race_key=? AND state='FROZEN'", (payload["race_key"],)).fetchone()
            if existing:
                raise ValueError("final frozen decision already exists for race")
            version = conn.execute("SELECT COALESCE(MAX(decision_version), 0) + 1 FROM decision_records WHERE race_key=?", (payload["race_key"],)).fetchone()[0]
            decision_id = str(uuid.uuid4())
            fixture = int(bool(payload.get("engineering_fixture")))
            conn.execute("INSERT INTO decision_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (decision_id, payload["race_key"], version, "FROZEN", payload["decision_status"], utc_iso(payload["decision_created_at"]), frozen, input_digest, payload["market_snapshot_id"], payload["current_snapshot_id"], payload["analysis_bundle_path"], payload["analysis_bundle_sha256"], payload["model_version"], payload["feature_set"], payload["model_artifact_sha256"], fixture, utc_iso(datetime.now(timezone.utc))))
            for row in payload["runner_predictions"]:
                conn.execute("INSERT INTO decision_runner_predictions VALUES(?,?,?,?,?,?)", (decision_id, int(row["horse_number"]), float(row["model_probability"]), float(row["market_probability"]), float(row["edge"]), int(row["rank"])))
            for row in payload["recommended_tickets"]:
                conn.execute("INSERT INTO decision_tickets VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), decision_id, row["ticket_type"], json.dumps(row["selections"], ensure_ascii=False, sort_keys=True), float(row["stake_units"]), row.get("reference_odds"), json.dumps(row.get("reason_codes", []), ensure_ascii=False, sort_keys=True)))
            event(conn, payload["race_key"], "DECISION_FROZEN", {"decision_id": decision_id, "frozen_at": frozen, "engineering_fixture": bool(fixture)})
        return decision_id
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze an already-created decision; no model inference is performed.")
    parser.add_argument("--input", required=True, type=Path); parser.add_argument("--db", default=DEFAULT_DB, type=Path); parser.add_argument("--market-db", default=MARKET_DB, type=Path)
    args = parser.parse_args()
    decision_id = freeze_decision(json.loads(args.input.read_text()), db_path=args.db, market_db=args.market_db)
    print(json.dumps({"decision_id": decision_id, "state": "FROZEN"}))


if __name__ == "__main__":
    main()
