import csv
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import archive_bytes, connect
from src.operations.pre_race_fallback import select_pre_race_reference
from src.operations.prospective_day_collector import ProspectiveDayCollector, RaceTask


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "manifests" / "NANKAN_OFFICIAL_FIXTURE_MANIFEST.csv"


def raw(kind: str) -> bytes:
    with MANIFEST.open(encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["fixture_kind"] == kind]
    return (ROOT / rows[-1]["raw_path"]).read_bytes()


class WideCaptureSetIntegrationTest(unittest.TestCase):
    def test_t15_persists_official_wide_from_same_current_capture_set(self):
        entry_raw, win_raw, wide_raw, trio_raw = raw("ENTRY"), raw("WIN"), raw("WIDE"), raw("TRIO")
        entry_url = "https://www.nankankeiba.com/syousai/2026073121050510.do"
        identity = official.resolve_race(entry_url, official.decode_html(entry_raw, "text/html"))
        task = RaceTask(
            entry_url=entry_url,
            identity=identity,
            scheduled_post_time=datetime(2026, 7, 31, 10, 40, tzinfo=timezone.utc),
        )
        captures = iter([entry_raw, win_raw, wide_raw, trio_raw])

        def fetch(url, timeout):
            payload = next(captures)
            moment = "2026-07-31T10:24:55+00:00"
            return official.FetchResult(url, moment, moment, url, [], 200, {"Content-Type": "text/html"}, payload)

        archive_ids = iter(["current-capture", "win-capture", "wide-capture", "trio-capture"])

        def archive(source_type, race_key, content, captured_at, content_type):
            return next(archive_ids), f"data/raw/test/{source_type}.html", len(content)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collector = ProspectiveDayCollector(
                race_date="2026-07-31", db_path=root / "market.sqlite", output_root=root / "out",
                fetch=fetch, printer=None,
            )
            with patch("src.operations.prospective_day_collector.archive_bytes", side_effect=archive):
                result = collector._capture(task, "T15")
            self.assertEqual(result["wide_market_status"], "COMPLETE")
            self.assertEqual(result["wide_market_pair_count"], 66)
            self.assertEqual(result["trio_market_status"], "COMPLETE")
            self.assertEqual(result["trio_market_ticket_count"], 220)
            con = connect(root / "market.sqlite")
            try:
                notes = json.loads(con.execute("SELECT notes FROM current_info_snapshots").fetchone()[0])
                self.assertEqual(notes["market_win_capture_id"], "win-capture")
                self.assertEqual(notes["market_wide_capture_id"], "wide-capture")
                self.assertEqual(notes["market_wide_status"], "COMPLETE")
                self.assertEqual(notes["market_trio_capture_id"], "trio-capture")
                self.assertEqual(notes["market_trio_status"], "COMPLETE")
                counts = dict(con.execute("SELECT bet_type_code,COUNT(*) FROM market_snapshots GROUP BY bet_type_code"))
                self.assertEqual(counts, {"TRIO": 220, "WIDE": 66, "WIN": 12})
                wide = con.execute("SELECT COUNT(*) FROM market_snapshots WHERE bet_type_code='WIDE' AND capture_id='wide-capture'").fetchone()[0]
                self.assertEqual(wide, 66)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM market_snapshots WHERE bet_type_code='TRIO' AND capture_id='trio-capture'").fetchone()[0], 220)
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                con.close()
            selected = select_pre_race_reference(
                db_path=root / "market.sqlite", race_date="2026-07-31", venue="川崎", race_number=10,
                now=datetime(2026, 7, 31, 10, 25, tzinfo=timezone.utc),
            )
            self.assertEqual(selected["reference"]["trio_capture_id"], "trio-capture")
            self.assertEqual(selected["reference"]["trio_capture_status"], "COMPLETE")
            self.assertEqual(len(selected["t15_trio_rows"]), 220)

    def _capture_saved_20260828_race12(self, *, expected_overrides: dict | None = None, truncate_win: bool = False) -> dict:
        """Run one saved official CURRENT/WIN/WIDE set against temporary state.

        The saved T20 response is used as a production-like fixture.  Metadata
        differences are only synthetic discovery-vs-current inputs for the
        field contract; this does not claim to reproduce the unavailable T15
        response from 2026-08-28.
        """
        current_raw = next((ROOT / "data/raw/current_info/2026/2026-08-28/船橋/race12").glob("*.html")).read_bytes()
        market_raw = sorted((ROOT / "data/raw/market_snapshots/2026/2026-08-28/船橋/race12").glob("*.html"))
        win_raw = wide_raw = None
        for value in market_raw:
            raw = value.read_bytes()
            html = official.decode_html(raw, "text/html")
            try:
                if len(official.parse_win_odds(html)) == 13:
                    win_raw = raw
            except ValueError:
                pass
            try:
                if len(official.parse_wide_odds(html)) == 78:
                    wide_raw = raw
            except ValueError:
                pass
        self.assertIsNotNone(win_raw)
        self.assertIsNotNone(wide_raw)
        entry_url = "https://www.nankankeiba.com/uma_shosai/2026082819060512.do"
        identity = official.resolve_race(entry_url, official.decode_html(current_raw, "text/html"))
        task = RaceTask(entry_url=entry_url, identity=identity | (expected_overrides or {}),
                        scheduled_post_time=datetime(2026, 8, 28, 11, 50, tzinfo=timezone.utc))
        times = iter(("2026-08-28T11:34:30+00:00", "2026-08-28T11:34:31+00:00", "2026-08-28T11:34:32+00:00"))

        def fetch(url, timeout):
            base = url.split("#", 1)[0]
            if url == entry_url:
                payload = current_raw
            elif base.endswith("202608281906051201.do"):
                payload = win_raw
            elif base.endswith("202608281906051204.do"):
                payload = wide_raw
            else:
                raise AssertionError(url)
            captured = next(times)
            return official.FetchResult(url, captured, captured, url, [], 200, {"Content-Type": "text/html"}, payload)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def archive(source_type, race_key, content, captured_at, content_type):
                return archive_bytes(source_type, race_key, content, captured_at, content_type, raw_root=root / "raw")

            collector = ProspectiveDayCollector(race_date="2026-08-28", db_path=root / "market.sqlite", output_root=root / "out", fetch=fetch, printer=None)
            with patch("src.operations.prospective_day_collector.archive_bytes", side_effect=archive):
                if truncate_win:
                    rows = official.parse_win_odds(official.decode_html(win_raw, "text/html"))
                    with patch("src.operations.prospective_day_collector.official.parse_win_odds", return_value=rows[:-1]):
                        try:
                            result, error = collector._capture(task, "T15"), None
                        except Exception as exc:  # hard/unknown drift path
                            result, error = None, exc
                else:
                    try:
                        result, error = collector._capture(task, "T15"), None
                    except Exception as exc:  # hard/unknown drift path
                        result, error = None, exc
            events = [json.loads(path.read_text(encoding="utf-8")) for path in (root / "out/2026-08-28/events").glob("*.json")]
            output = {
                "result": result, "error": error, "events": events,
                "metadata_raw_archives_exist": all(
                    Path(item["raw_archive_path"]).exists()
                    for item in events if item.get("phase") == "RACE_METADATA_DRIFT"
                ),
            }
            if result is not None:
                selected = select_pre_race_reference(
                    db_path=root / "market.sqlite", race_date="2026-08-28", venue="船橋", race_number=12,
                    now=datetime(2026, 8, 28, 11, 35, tzinfo=timezone.utc),
                )
                con = connect(root / "market.sqlite")
                try:
                    output["selected"] = selected
                    output["snapshot"] = dict(con.execute("SELECT scheduled_post_time,target_decision_label,t15_timing_status,notes FROM current_info_snapshots").fetchone())
                    output["source_notes"] = json.loads(con.execute("SELECT notes FROM source_captures WHERE source_type='CURRENT_INFO'").fetchone()[0])
                    output["market_counts"] = dict(con.execute("SELECT bet_type_code,COUNT(*) FROM market_snapshots GROUP BY bet_type_code"))
                finally:
                    con.close()
            return output

    def test_no_metadata_drift_preserves_t15_standard(self):
        observed = self._capture_saved_20260828_race12()
        self.assertIsNone(observed["error"])
        self.assertEqual(observed["result"]["status"], "COMPLETE")
        self.assertEqual(observed["result"]["metadata_drift"], [])
        self.assertTrue(observed["result"]["scientific_sample"])
        self.assertEqual(observed["selected"]["reference"]["mode"], "T15_STANDARD")
        self.assertTrue(observed["selected"]["reference"]["scientific_sample"])
        self.assertFalse(observed["result"]["outcome_accessed"])

    def test_field_size_drift_continues_and_active_roster_validation_remains_required(self):
        observed = self._capture_saved_20260828_race12(expected_overrides={"field_size": 14})
        self.assertEqual(observed["result"]["status"], "COMPLETE")
        self.assertEqual(observed["result"]["metadata_drift"][0]["classification"], "ALLOWED_MUTABLE")
        self.assertEqual(observed["result"]["metadata_drift"][0]["action"], "CONTINUE")
        self.assertEqual(observed["source_notes"]["race_metadata_drift"][0]["old_value"], 14)
        self.assertEqual(observed["source_notes"]["race_metadata_drift"][0]["new_value"], 13)
        self.assertEqual(observed["market_counts"], {"WIDE": 78, "WIN": 13})
        incomplete = self._capture_saved_20260828_race12(expected_overrides={"field_size": 14}, truncate_win=True)
        self.assertEqual(incomplete["result"]["status"], "PARTIAL")
        self.assertFalse(incomplete["result"]["market_current_roster_match"])

    def test_presentation_only_race_name_drift_continues(self):
        observed = self._capture_saved_20260828_race12(expected_overrides={"race_name": "表示名のみ変更"})
        self.assertEqual(observed["result"]["status"], "COMPLETE")
        self.assertEqual(observed["result"]["metadata_drift"][0]["classification"], "PRESENTATION_ONLY")
        self.assertEqual(observed["result"]["metadata_drift"][0]["action"], "CONTINUE")
        event = next(item for item in observed["events"] if item.get("phase") == "RACE_METADATA_DRIFT")
        self.assertEqual(event["metadata_drift"][0]["old_value"], "表示名のみ変更")
        self.assertEqual(event["metadata_drift"][0]["new_value"], observed["result"]["metadata_drift"][0]["new_value"])

    def test_scheduled_post_drift_is_explicit_non_scientific_t15_fallback(self):
        observed = self._capture_saved_20260828_race12(expected_overrides={"scheduled_post_time_local": "20:55"})
        self.assertEqual(observed["result"]["status"], "COMPLETE")
        self.assertEqual(observed["result"]["metadata_drift"][0]["classification"], "TIMING_MUTABLE")
        self.assertEqual(observed["result"]["metadata_drift"][0]["action"], "CONTINUE_PRE_RACE_FALLBACK")
        self.assertEqual(observed["source_notes"]["race_metadata_drift"][0]["old_value"], "20:55")
        self.assertEqual(observed["source_notes"]["race_metadata_drift"][0]["new_value"], "20:50")
        self.assertEqual(observed["result"]["fallback_reason"], "SCHEDULED_POST_TIME_DRIFT")
        self.assertFalse(observed["result"]["scientific_sample"])
        self.assertEqual(observed["snapshot"]["scheduled_post_time"], "2026-08-28T11:50:00+00:00")
        self.assertEqual(observed["snapshot"]["target_decision_label"], "PRE_RACE_FALLBACK")
        self.assertEqual(observed["snapshot"]["t15_timing_status"], "UNCLASSIFIED")
        self.assertEqual(observed["selected"]["reference"]["mode"], "PRE_RACE_FALLBACK")
        self.assertEqual(observed["selected"]["reference"]["source_mark"], "T15")
        self.assertEqual(observed["selected"]["reference"]["fallback_reason"], "SCHEDULED_POST_TIME_DRIFT")
        self.assertFalse(observed["selected"]["reference"]["scientific_sample"])
        self.assertEqual(observed["selected"]["reference"]["captured_mark"], "T15")

    def test_hard_and_unknown_metadata_drift_block_with_field_evidence(self):
        for field, value in (("distance_m", 1600), ("surface", "芝"), ("conditions_raw", "Ｃ１"), ("unclassified_source_field", "old")):
            with self.subTest(field=field):
                observed = self._capture_saved_20260828_race12(expected_overrides={field: value})
                self.assertIsNone(observed["result"])
                self.assertIn("RACE_IDENTITY_CHANGED_DURING_DAY_COLLECTION", str(observed["error"]))
                event = next(item for item in observed["events"] if item.get("phase") == "RACE_METADATA_DRIFT")
                self.assertEqual(event["action"], "BLOCK")
                diff = next(item for item in event["metadata_drift"] if item["field"] == field)
                self.assertEqual(diff["old_value"], value)
                self.assertEqual(diff["classification"], "UNCLASSIFIED" if field == "unclassified_source_field" else "HARD_INVARIANT")
                self.assertEqual(diff["action"], "BLOCK")
                self.assertEqual(event["race_key"], "2026-08-28_船橋_12")
                self.assertEqual(event["capture_mark"], "T15")
                self.assertEqual(event["captured_at"], "2026-08-28T11:34:30+00:00")
                self.assertTrue(observed["metadata_raw_archives_exist"])

    def test_saved_race12_metadata_drift_fresh_process_smoke(self):
        names = [
            "test_no_metadata_drift_preserves_t15_standard",
            "test_field_size_drift_continues_and_active_roster_validation_remains_required",
            "test_scheduled_post_drift_is_explicit_non_scientific_t15_fallback",
            "test_hard_and_unknown_metadata_drift_block_with_field_evidence",
        ]
        command = [sys.executable, "-m", "unittest", *[
            f"tests.integration.test_p2_wide_ops_v0_capture_set.WideCaptureSetIntegrationTest.{name}" for name in names
        ]]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
