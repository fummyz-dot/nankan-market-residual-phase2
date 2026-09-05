import tempfile
import unittest
from pathlib import Path

from src.ingestion.prospective_store import connect, initialize_database, record_capture, record_market_snapshot, register_race


class MarketSnapshotSchemaTest(unittest.TestCase):
    def test_v2_schema_and_post_time_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "market.sqlite"
            initialize_database(db)
            conn = connect(db)
            race_id = register_race(conn, race_date="2026-08-18", venue="大井", race_number=9, scheduled_post_time="2026-08-18T20:00:00+09:00", scheduled_post_time_source="USER", scheduled_post_time_captured_at="2026-08-18T10:00:00+09:00")
            capture_id = record_capture(conn, race_registry_id=race_id, source_type="MARKET", source_name="TEST", source_reference=None, submitted_url=None, requested_at="2026-08-18T10:00:00+09:00", captured_at="2026-08-18T10:01:00+09:00", source_published_at=None, http_status=200, content_type="text/html", encoding="utf-8", raw_archive_path_value="data/raw/test.html", raw_sha256="a" * 64, response_size_bytes=1, capture_status="COLLECTED_OK")
            snapshot_id = record_market_snapshot(conn, race_registry_id=race_id, capture_id=capture_id, bet_type_code="WIN", normalized_combination_key="01", captured_at="2026-08-18T19:40:00+09:00", scheduled_post_time="2026-08-18T20:00:00+09:00", snapshot_role="PRIMARY_CANDIDATE", target_decision_time="T-15_ENGINEERING_CANDIDATE", response_sha256="a" * 64, availability_status="CAPTURED_TIME_ONLY", quality_status="UNPARSED_SOURCE_ADAPTER_PENDING")
            self.assertEqual(conn.execute("SELECT snapshot_role FROM market_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()[0], "PRIMARY_CANDIDATE")
            with self.assertRaises(ValueError):
                record_market_snapshot(conn, race_registry_id=race_id, capture_id=capture_id, bet_type_code="WIN", normalized_combination_key="02", captured_at="2026-08-18T20:01:00+09:00", scheduled_post_time="2026-08-18T20:00:00+09:00", snapshot_role="PRIMARY_CANDIDATE", target_decision_time="T-15_ENGINEERING_CANDIDATE", response_sha256="b" * 64, availability_status="CAPTURED_TIME_ONLY", quality_status="UNPARSED")
            conn.close()
