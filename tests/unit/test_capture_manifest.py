import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ingestion import prospective_store as store


class CaptureManifestTest(unittest.TestCase):
    def test_append_only_raw_archive_hash_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            captured = "2026-08-18T10:00:00+09:00"
            with patch.object(store, "RAW_ROOT", root / "raw"), patch.object(store, "MANIFEST_PATH", root / "manifest.csv"):
                capture_id, path, size = store.archive_bytes("BODY_WEIGHT", "2026-08-18_大井_09", b'{"x":1}', captured, "application/json")
                self.assertEqual(size, 7)
                self.assertEqual((root / path).read_bytes(), b'{"x":1}')
                store.append_manifest(capture_id=capture_id, source_type="BODY_WEIGHT", race_key="2026-08-18_大井_09", captured_at=captured, source_reference="https://example.invalid", raw_path=path, size_bytes=size, sha256=store.sha256_bytes(b'{"x":1}'), collector_version="test", parser_version="pending", status="COLLECTED_OK")
                with (root / "manifest.csv").open(encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(rows[0]["sha256"], store.sha256_bytes(b'{"x":1}'))
