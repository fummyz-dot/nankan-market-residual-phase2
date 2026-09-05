import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ingestion import prospective_store as store


class ProspectiveCaptureFlowTest(unittest.TestCase):
    def test_synthetic_raw_capture_to_registry_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "market.sqlite"
            store.initialize_database(db)
            conn = store.connect(db)
            race_id = store.register_race(conn, race_date="2026-08-18", venue="船橋", race_number=2, scheduled_post_time="2026-08-18T18:00:00+09:00", scheduled_post_time_source="SYNTHETIC", scheduled_post_time_captured_at="2026-08-18T10:00:00+09:00")
            with patch.object(store, "RAW_ROOT", root / "raw"), patch.object(store, "MANIFEST_PATH", root / "manifest.csv"):
                capture_id, raw_path, size = store.archive_bytes("BODY_WEIGHT", "2026-08-18_船橋_02", b"raw", "2026-08-18T10:05:00+09:00")
                store.record_capture(conn, race_registry_id=race_id, source_type="BODY_WEIGHT", source_name="SYNTHETIC", source_reference="fixture", submitted_url="fixture://bodyweight", requested_at="2026-08-18T10:04:00+09:00", captured_at="2026-08-18T10:05:00+09:00", source_published_at=None, http_status=200, content_type="text/html", encoding="utf-8", raw_archive_path_value=raw_path, raw_sha256=store.sha256_bytes(b"raw"), response_size_bytes=size, capture_status="COLLECTED_OK", capture_id=capture_id)
                self.assertEqual(conn.execute("SELECT raw_sha256 FROM source_captures").fetchone()[0], store.sha256_bytes(b"raw"))
            conn.close()
