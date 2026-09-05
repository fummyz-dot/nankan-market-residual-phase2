"""Regression guard: M12B cannot silently introduce name-only live identity matching."""

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_current_snapshot_schema_has_no_birth_date_and_requires_blocker_resolution():
    conn = sqlite3.connect(ROOT / "db/market_snapshot.sqlite")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(current_runner_info)")}
    finally:
        conn.close()
    assert "birth_date" not in columns
    report = (ROOT / "reports/development/P2_M12B_ONLINE_SHADOW_PIPELINE_BLOCKER_REPORT.md").read_text(encoding="utf8")
    assert "name-only/fuzzy identity path" in report


def test_current_keibabook_payload_has_no_birth_date_for_live_identity():
    inbox = ROOT / "data/raw/keibabook/inbox/2026-08-20/keibabook_chihou_nouryoku_20260820_6races.json"
    document = json.loads(inbox.read_text(encoding="utf8"))
    horse = document["races"][0]["horses"][0]["horse"]
    assert "name" in horse and "birth_date" not in horse
