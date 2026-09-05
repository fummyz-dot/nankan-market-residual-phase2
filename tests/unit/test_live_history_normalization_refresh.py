import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from src.operations import live_history_update
from src.operations import normalize_live_history_delta
from src.operations import official_result_collector
from src.operations.normalize_live_history_delta import AUDIT
from src.ingestion.adapters import nankan_official as official
from src.ingestion.adapters.nankan_official import FetchResult


ROOT = Path(__file__).resolve().parents[2]


class LiveHistoryNormalizationRefreshTest(unittest.TestCase):
    @staticmethod
    def _normalized(path: Path, keys: tuple[str, ...] = ()) -> None:
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE races(race_key TEXT PRIMARY KEY)")
        con.execute("CREATE TABLE race_runners(id INTEGER)")
        con.executemany("INSERT INTO races VALUES(?)", [(key,) for key in keys])
        con.commit(); con.close()

    @staticmethod
    def _response(url: str, html: str) -> FetchResult:
        return FetchResult(url, "2026-08-24T00:00:00+00:00", "2026-08-24T00:00:00+00:00", url, [], 200, {"Content-Type": "text/html"}, html.encode())

    def test_incremental_discovery_finds_new_meeting_after_previous_manifest(self):
        calendar = '<a href="/program/20260821210604.do">8/21</a>'
        program = '<a href="/syousai/2026082121060401.do">card</a><a href="/result/2026082121060401.do">result</a>'
        def fetch(url, _timeout=15):
            return self._response(url, calendar if url == live_history_update.CALENDAR_URL else program)
        days, _ = live_history_update.discover_meeting_days("2026-08-21", "2026-08-23", fetch=fetch)
        self.assertEqual([day["official_calendar_status"] for day in days], ["MEETING_PRESENT", "NO_SOUTH_KANTO_MEETING", "NO_SOUTH_KANTO_MEETING"])
        self.assertEqual(days[0]["races"], [("https://www.nankankeiba.com/syousai/2026082121060401.do", "https://www.nankankeiba.com/result/2026082121060401.do")])

    def test_no_meeting_day_accounted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw, normalized = root / "raw.sqlite", root / "normalized.sqlite"
            live_history_update.initialize(raw); self._normalized(normalized)
            record = live_history_update._write_day_ledger(
                db_path=raw, normalized_db=normalized,
                day={"race_date": "2026-08-22", "official_calendar_status": "NO_SOUTH_KANTO_MEETING", "races": []},
                provenance={"calendar_source_url": "official", "calendar_source_sha256": "x"},
            )
            self.assertEqual(record["status"], "NO_MEETING")

    def test_missing_expected_meeting_blocks_freshness_even_when_counts_equal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw, normalized, audit = root / "raw.sqlite", root / "normalized.sqlite", root / "audit"
            audit.mkdir(); live_history_update.initialize(raw); self._normalized(normalized)
            live_history_update._write_day_ledger(
                db_path=raw, normalized_db=normalized,
                day={"race_date": "2026-08-21", "official_calendar_status": "MEETING_PRESENT", "races": [("card", "https://official/result/2026082121060401.do")]},
                provenance={"calendar_source_url": "official", "calendar_source_sha256": "x"},
            )
            with patch.object(normalize_live_history_delta, "AUDIT", audit):
                with self.assertRaisesRegex(RuntimeError, "LIVE_HISTORY_STALE"):
                    normalize_live_history_delta.record_meeting_aware_freshness(through="2026-08-21", raw_delta=raw, normalized_db=normalized)

    def test_equal_raw_normalized_counts_do_not_imply_official_freshness(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw, normalized, audit = root / "raw.sqlite", root / "normalized.sqlite", root / "audit"
            audit.mkdir(); self._normalized(raw); self._normalized(normalized)
            (audit / "live_history_normalization_status.json").write_text('{"status":"NORMALIZED_HISTORY_FRESH"}', encoding="utf-8")
            with patch.object(normalize_live_history_delta, "AUDIT", audit):
                with self.assertRaisesRegex(RuntimeError, "LIVE_HISTORY_STALE"):
                    normalize_live_history_delta.assert_normalized_fresh(raw_delta=raw, normalized_db=normalized)

    def test_raw_ahead_of_normalized_cache_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw, normalized = root / "raw.sqlite", root / "normalized.sqlite"
            for path, count in ((raw, 2), (normalized, 1)):
                con = sqlite3.connect(path); con.execute("CREATE TABLE races(id INTEGER)"); con.execute("CREATE TABLE race_runners(id INTEGER)")
                con.executemany("INSERT INTO races VALUES(?)", [(i,) for i in range(count)])
                con.executemany("INSERT INTO race_runners VALUES(?)", [(i,) for i in range(count)])
                con.commit(); con.close()
            audit = root / "audit"; audit.mkdir(); (audit / "live_history_normalization_status.json").write_text(
                '{"status":"LIVE_HISTORY_FRESH","official_meeting_history_complete":"PASS","normalized_cache_current":"PASS","meeting_history_through":"2026-08-23"}',
                encoding="utf-8",
            )
            with patch.object(normalize_live_history_delta, "AUDIT", audit):
                with self.assertRaisesRegex(RuntimeError, "LIVE_HISTORY_NORMALIZATION_STALE"):
                    normalize_live_history_delta.assert_normalized_fresh(raw_delta=raw, normalized_db=normalized)


class LiveHistorySavedResultFallbackTest(unittest.TestCase):
    """Real 2026-08-28 船橋12R saved/final result regression fixtures.

    The final fixture is a byte-identical copy of
    /tmp/p2_20260828_funabashi12_full_result.html (SHA-256
    574cbbe1b6d4ca264f9fe3e7c17db9cbbd2009dacd05d76ccfdbb570112bf9ff).
    The partial fixture remains the immutable saved settlement raw in data/.
    """

    RACE_ID = "2026082819060512"
    CARD_URL = f"https://www.nankankeiba.com/syousai/{RACE_ID}.do"
    RESULT_URL = f"https://www.nankankeiba.com/result/{RACE_ID}.do"
    SAVED_PARTIAL = ROOT / "data/raw/live_development_results/P2_RACE_V1::2026-08-28\x1f船橋\x1f12/result_20260828T120430.445112+0000_be602e1e6b0510a5ed61850d87a5e971c5ecd530782b56e48adde856cb09cb3e.html"
    CARD = ROOT / "data/raw/current_info/2026/2026-08-28/船橋/race12/current_info_20260828T112930456271Z_bec7d5de-d694-408f-affe-62619ca52492.html"
    FULL = ROOT / "tests/fixtures/nankan_official/funabashi_20260828_race12_final_result.html"

    def setUp(self):
        self.card_raw = self.CARD.read_bytes()
        self.partial_raw = self.SAVED_PARTIAL.read_bytes()
        self.full_raw = self.FULL.read_bytes()
        self.assertEqual(hashlib.sha256(self.full_raw).hexdigest(), "574cbbe1b6d4ca264f9fe3e7c17db9cbbd2009dacd05d76ccfdbb570112bf9ff")
        self.detail_raw = {}
        for horse_id_dir in (ROOT / "data/raw/current_identity_details").iterdir():
            path = next(horse_id_dir.glob("detail_*.html"), None)
            if path is not None:
                self.detail_raw[f"https://www.nankankeiba.com/uma_info/{horse_id_dir.name}.do"] = path.read_bytes()

    @staticmethod
    def _response(url: str, raw: bytes) -> FetchResult:
        return FetchResult(url, "2026-08-31T00:00:00+00:00", "2026-08-31T00:00:00+00:00", url, [], 200, {"Content-Type": "text/html"}, raw)

    def _fetcher(self, fresh_result: bytes):
        calls: Counter[str] = Counter()

        def fetch(url: str, _timeout=15) -> FetchResult:
            calls[url] += 1
            if url == self.CARD_URL:
                return self._response(url, self.card_raw)
            if url == self.RESULT_URL:
                return self._response(url, fresh_result)
            if url in self.detail_raw:
                return self._response(url, self.detail_raw[url])
            raise AssertionError(f"unexpected URL: {url}")

        return calls, fetch

    def _saved_result(self, root: Path, raw: bytes) -> None:
        race_key = "P2_RACE_V1::2026-08-28\x1f船橋\x1f12"
        destination = root / race_key / f"result_20260828T120430.445112+0000_{hashlib.sha256(raw).hexdigest()}.html"
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.SAVED_PARTIAL if raw == self.partial_raw else self.FULL
        shutil.copyfile(source, destination)

    def _ingest(self, *, saved: bytes | None, fresh: bytes, db: Path, root: Path):
        saved_root = root / "saved_results"
        if saved is not None:
            self._saved_result(saved_root, saved)
        calls, fetch = self._fetcher(fresh)
        self._last_calls = calls
        with ExitStack() as patches:
            patches.enter_context(patch.object(live_history_update, "ROOT", root))
            patches.enter_context(patch.object(live_history_update, "RAW_ROOT", root / "raw"))
            patches.enter_context(patch.object(official_result_collector, "RESULT_RAW_ROOT", saved_root))
            outcome = live_history_update.ingest_race(self.CARD_URL, self.RESULT_URL, db_path=db, fetch=fetch)
        return outcome, calls

    def test_partial_saved_result_falls_back_to_real_full_result_and_commits_full_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "delta.sqlite"
            identity = official.resolve_race(self.CARD_URL, official.decode_html(self.card_raw, "text/html"))
            _, partial_rows = official.parse_history_result_raw_rows(official.decode_html(self.partial_raw, "text/html"), identity=identity)
            self.assertEqual([row["horse_number"] for row in partial_rows], [12, 7, 13])
            with self.assertRaisesRegex(ValueError, "RESULT_HISTORY_RUNNER_ROSTER_UNRESOLVED"):
                official.parse_history_result_fields(official.decode_html(self.partial_raw, "text/html"), identity=identity)
            full_history = official.parse_history_result_fields(official.decode_html(self.full_raw, "text/html"), identity=identity)
            self.assertEqual([row["horse_number"] for row in full_history["runners"]], [12, 7, 13, 3, 11, 5, 10, 9, 2, 6, 8, 1, 4])
            self.assertEqual(len(full_history["runners"]), identity["field_size"])
            outcome, calls = self._ingest(saved=self.partial_raw, fresh=self.full_raw, db=db, root=root)
            self.assertEqual(outcome["status"], "RESULT_OFFICIAL_FINAL")
            self.assertEqual(outcome["runners"], 13)
            self.assertFalse(outcome["saved_result_raw_reused"])
            self.assertEqual(outcome["saved_result_raw_reuse_rejected_reason"], "ValueError:RESULT_HISTORY_RUNNER_ROSTER_UNRESOLVED")
            self.assertEqual(calls[self.RESULT_URL], 1)
            con = sqlite3.connect(db)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM race_runners").fetchone()[0], 13)
                result_hash = con.execute("SELECT raw_sha256 FROM source_captures WHERE source_type='OFFICIAL_RESULT'").fetchone()[0]
            finally:
                con.close()
            self.assertEqual(result_hash, hashlib.sha256(self.full_raw).hexdigest())
            self.assertNotEqual(result_hash, hashlib.sha256(self.partial_raw).hexdigest())

    def test_complete_saved_result_skips_fresh_result_fetch(self):
        with tempfile.TemporaryDirectory() as temporary:
            outcome, calls = self._ingest(saved=self.full_raw, fresh=self.partial_raw, db=Path(temporary) / "delta.sqlite", root=Path(temporary))
            self.assertEqual(outcome["status"], "RESULT_OFFICIAL_FINAL")
            self.assertTrue(outcome["saved_result_raw_reused"])
            self.assertNotIn("saved_result_raw_reuse_rejected_reason", outcome)
            self.assertEqual(calls[self.RESULT_URL], 0)

    def test_partial_saved_and_partial_fresh_result_fails_without_delta_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "delta.sqlite"
            with self.assertRaisesRegex(ValueError, "RESULT_HISTORY_RUNNER_ROSTER_UNRESOLVED"):
                self._ingest(saved=self.partial_raw, fresh=self.partial_raw, db=db, root=root)
            self.assertEqual(self._last_calls[self.RESULT_URL], 1)
            self.assertFalse(db.exists())

    def test_no_saved_result_uses_fresh_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            outcome, calls = self._ingest(saved=None, fresh=self.full_raw, db=Path(temporary) / "delta.sqlite", root=Path(temporary))
            self.assertEqual(outcome["status"], "RESULT_OFFICIAL_FINAL")
            self.assertFalse(outcome["saved_result_raw_reused"])
            self.assertNotIn("saved_result_raw_reuse_rejected_reason", outcome)
            self.assertEqual(calls[self.RESULT_URL], 1)

    def test_fallback_replay_is_idempotent_without_conflicting_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "delta.sqlite"
            first, first_calls = self._ingest(saved=self.partial_raw, fresh=self.full_raw, db=db, root=root)
            second, second_calls = self._ingest(saved=self.partial_raw, fresh=self.full_raw, db=db, root=root)
            self.assertEqual(first["status"], "RESULT_OFFICIAL_FINAL")
            self.assertEqual(second["status"], "IDEMPOTENT_NOOP")
            self.assertEqual(second["saved_result_raw_reuse_rejected_reason"], "ValueError:RESULT_HISTORY_RUNNER_ROSTER_UNRESOLVED")
            self.assertEqual(first_calls[self.RESULT_URL], 1)
            self.assertEqual(second_calls[self.RESULT_URL], 1)
            con = sqlite3.connect(db)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM races").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM race_runners").fetchone()[0], 13)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM source_captures WHERE source_type='OFFICIAL_RESULT'").fetchone()[0], 1)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
