import tempfile
import unittest
from pathlib import Path

from src.ingestion.prospective_store import connect, initialize_database, primary_candidate_eligible, record_capture, record_market_snapshot, register_race


class MarketSnapshotRoundtripTest(unittest.TestCase):
    def test_snapshot_roundtrip_and_candidate_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "market.sqlite"
            initialize_database(db); conn = connect(db)
            race = register_race(conn, race_date="2026-08-18", venue="浦和", race_number=1, scheduled_post_time="2026-08-18T17:00:00+09:00", scheduled_post_time_source="test", scheduled_post_time_captured_at="2026-08-18T09:00:00+09:00")
            cap = record_capture(conn, race_registry_id=race, source_type="MARKET", source_name="test", source_reference=None, submitted_url=None, requested_at="2026-08-18T09:00:00+09:00", captured_at="2026-08-18T09:01:00+09:00", source_published_at=None, http_status=200, content_type="text/html", encoding=None, raw_archive_path_value="x", raw_sha256="c" * 64, response_size_bytes=1, capture_status="COLLECTED_OK")
            record_market_snapshot(conn, race_registry_id=race, capture_id=cap, bet_type_code="WIDE", normalized_combination_key="01-02", captured_at="2026-08-18T16:45:00+09:00", scheduled_post_time="2026-08-18T17:00:00+09:00", snapshot_role="PRIMARY_CANDIDATE", target_decision_time="T-15_ENGINEERING_CANDIDATE", response_sha256="c" * 64, availability_status="CAPTURED_TIME_ONLY", quality_status="PENDING")
            self.assertTrue(primary_candidate_eligible("PRIMARY_CANDIDATE", "2026-08-18T16:45:00+09:00", "2026-08-18T17:00:00+09:00"))
            conn.close()
