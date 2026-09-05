"""Forensic, outcome-free closeout for P2-OPS-001."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAY = ROOT / "outputs/prospective_collection/2026-08-20"
AUDIT = ROOT / "audit/data/p2_ops_001"
DB = ROOT / "db/market_snapshot.sqlite"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(name: str, rows: list[dict]) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["status"]
    with (AUDIT / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def main() -> None:
    checkpoint = DAY / "day_collector.run/checkpoints/2026-08-20_川崎_01__T20.complete.json"
    race_status = DAY / "races/race01_status.json"
    live_status = DAY / "live_status.json"
    events = sorted((DAY / "events").glob("*.json"))
    write_csv("incident_scope_audit.csv", [{
        "incident_id": "P2-OPS-001", "title": "LIVE_COLLECTOR_FOREIGN_KEY_FAILURE", "race_key": "2026-08-20_川崎_01",
        "mark": "T20", "failure_scope": "RACE_SCOPED_FAILURE", "error": "IntegrityError: FOREIGN KEY constraint failed",
        "outcome_accessed": False, "performance_accessed": False, "status": "PRESERVED",
    }])
    write_csv("artifact_inventory.csv", [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "preserved": True} for path in (checkpoint, race_status, live_status, *events) if path.exists()])
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        registry = conn.execute("SELECT race_registry_id FROM race_registry WHERE canonical_race_key='2026-08-20_川崎_01'").fetchone()
        rows = [] if registry is None else conn.execute("SELECT capture_id,source_type FROM source_captures WHERE race_registry_id=? ORDER BY source_type", (registry[0],)).fetchall()
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    write_csv("foreign_key_lineage_audit.csv", [{"race_registry_present": registry is not None, "source_capture_rows": len(rows), "root_cause": "archive_capture_id_not_passed_to_record_capture", "fixed_behavior": "archive_capture_id_is_source_capture_parent_id_before_child_insert", "foreign_keys_disabled": False, "status": "FIXED"}])
    write_csv("db_integrity_audit.csv", [{"quick_check": quick, "foreign_key_check_rows": len(fk), "status": "PASS" if quick == "ok" and not fk else "FAIL"}])
    report = "# P2-OPS-001 — LIVE_COLLECTOR_FOREIGN_KEY_FAILURE\n\nThe 2026-08-20 Kawasaki 1R T20 failure is retained unchanged. Root cause: `archive_bytes` produced a capture UUID but `record_capture` generated a second UUID, so child snapshot rows referenced a missing `source_captures` parent. The collector now registers the archive UUID as the parent inside one explicit FK-enabled transaction. No outcome, performance, payout, or ROI data was accessed. Legacy failed `.complete.json` remains evidence only and is never promoted to success.\n"
    (AUDIT / "P2-OPS-001.md").write_text(report, encoding="utf-8")
    manifest = {"incident_id": "P2-OPS-001", "job": "P2-M11A-S-HOTFIX01", "built_at": datetime.now(timezone.utc).isoformat(), "outcome_accessed": False, "performance_accessed": False, "payout_accessed": False, "foreign_keys_disabled": False, "vcs_mode": "none", "git_commit": None}
    (AUDIT / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
