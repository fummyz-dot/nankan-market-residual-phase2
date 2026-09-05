import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.operations.live_freshness_probe import LiveFreshnessProbe, ProbeConfig, MARKS, mark_times

ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    def __init__(self, now): self.value = now; self.sleeps = []; self.mono = 0.0
    def now(self): return self.value
    def monotonic(self): return self.mono
    def sleep(self, seconds): self.sleeps.append(seconds); self.value = self.value.fromtimestamp(self.value.timestamp() + seconds, timezone.utc); self.mono += seconds


class FixtureFetcher:
    def __init__(self, clock, fail_once=None, timeout=False):
        self.clock, self.fail_once, self.timeout, self.calls = clock, fail_once, timeout, 0
        with (ROOT / "data/manifests/NANKAN_OFFICIAL_FIXTURE_MANIFEST.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.raw = {kind: (ROOT / [row for row in rows if row["fixture_kind"] == kind][-1]["raw_path"]).read_bytes() for kind in ("ENTRY", "WIN", "WIDE", "TRIO")}
    def fetch(self, url, timeout_seconds):
        self.calls += 1
        if self.fail_once and self.fail_once in url:
            self.fail_once = None; raise TimeoutError("synthetic fetch failure")
        if self.timeout and self.calls > 1: self.clock.mono += timeout_seconds + 1
        kind = "ENTRY" if "/syousai/" in url or "/uma_shosai/" in url else "WIDE" if url.split("#")[0].endswith("1004.do") else "TRIO" if url.split("#")[0].endswith("1009.do") else "WIN"
        now = self.clock.now().isoformat(); final = "https://www.nankankeiba.com/uma_shosai/2026073121050510.do" if kind == "ENTRY" else url
        return official.FetchResult(url, now, now, final, [{"status_code": 302}] if kind == "ENTRY" and "/syousai/" in url else [], 200, {"Content-Type": "text/html", "Date": "fixture"}, self.raw[kind])


def make_probe(now, marks=MARKS, **kwargs):
    temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name); clock = FakeClock(now); fetcher = FixtureFetcher(clock, **kwargs)
    config = ProbeConfig("https://www.nankankeiba.com/syousai/2026073121050510.do", db_path=root / "probe.sqlite", output_root=root / "outputs", raw_root=root / "raw", request_timeout_seconds=2, max_initial_wait_seconds=45 * 60, marks=marks)
    return temporary, clock, fetcher, LiveFreshnessProbe(config, clock=clock, fetcher=fetcher, printer=None)


class LiveFreshnessProbeTest(unittest.TestCase):
    def test_schedule_marks(self):
        post = datetime.fromisoformat("2026-07-31T10:40:00+00:00")
        self.assertEqual({key: value.strftime("%H:%M") for key, value in mark_times(post).items()}, {"T20": "10:20", "T15": "10:25", "T10": "10:30", "T05": "10:35"})

    def test_too_early(self):
        temp, clock, _, probe = make_probe(datetime.fromisoformat("2026-07-31T09:00:00+00:00"))
        with temp:
            result = probe.run(); self.assertEqual(result["overall_status"], "TOO_EARLY_TO_START"); self.assertEqual(clock.sleeps, [])

    def test_start_after_t20_and_no_false_backfill(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:23:00+00:00"), marks=MARKS[:2])
        with temp:
            result = probe.run(); self.assertEqual(result["captures"]["T20"]["status"], "MISSED_BEFORE_START")
            checkpoint = next((Path(temp.name) / "outputs").rglob("T20.complete.json"), None); self.assertIsNone(checkpoint)

    def test_monotonic_wait(self):
        temp, clock, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"))
        with temp:
            result = probe.run(); self.assertEqual(clock.sleeps, [300.0, 300.0, 300.0]); self.assertEqual(result["comparison"]["quote_change_counts"]["T20->T15"]["WIN"], 0); self.assertEqual(result["comparison"]["quote_change_counts"]["T20->T15"]["WIDE"], 0); self.assertEqual(result["comparison"]["quote_change_counts"]["T20->T15"]["TRIO"], 0)

    def test_capture_all_sources_and_expected_ticket_counts(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), marks=MARKS[:1])
        with temp:
            result = probe.run(); capture = result["captures"]["T20"]
            self.assertEqual(capture["status"], "PASS"); self.assertEqual(capture["win_summary"]["parsed"], 12); self.assertEqual(capture["wide_summary"]["parsed"], 66); self.assertEqual(capture["trio_summary"]["parsed"], 220); self.assertEqual(set(capture["raw_hashes"]), {"ENTRY", "WIN", "WIDE", "TRIO"})

    def test_partial_fetch_failure_continues(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), marks=MARKS[:2], fail_once="1004.do")
        with temp:
            result = probe.run(); self.assertEqual(result["captures"]["T20"]["status"], "CAPTURE_FAILED"); self.assertEqual(result["captures"]["T15"]["status"], "PASS")

    def test_request_timeout(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), marks=MARKS[:1], timeout=True)
        with temp:
            result = probe.run(); self.assertEqual(result["captures"]["T20"]["status"], "CAPTURE_FAILED"); self.assertEqual(result["captures"]["T20"]["errors"][0]["code"], "TimeoutError")

    def test_checkpoint_atomicity_and_run_marker_cleanup(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), marks=MARKS[:1])
        with temp:
            result = probe.run(); run_dirs = list((Path(temp.name) / "outputs").rglob("*.run")); self.assertEqual(len(run_dirs), 1); run_dir = run_dirs[0]
            self.assertTrue((run_dir / "T20.complete.json").exists()); self.assertTrue((run_dir / "COMPLETE.json").exists()); self.assertFalse((run_dir / "RUNNING.json").exists()); self.assertEqual(list(run_dir.glob("*.tmp")), []); self.assertEqual(result["overall_status"], "COMPLETE")

    def test_no_background_process(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), marks=MARKS[:1])
        with temp:
            result = probe.run(); self.assertEqual(result["process"], {"background_processes_used": 0, "child_processes_started": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0})

    def test_bodyweight_quarantine(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), marks=MARKS[:1])
        with temp:
            result = probe.run(); rows = result["captures"]["T20"]["bodyweight"]["runners"]; self.assertEqual(set(rows[0]), {"horse_number", "body_weight", "body_weight_change"})

    def test_primary_candidate_not_frozen(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), marks=MARKS[:2])
        with temp:
            result = probe.run(); t15 = result["captures"]["T15"]; self.assertEqual(t15["snapshot_role"], "PRIMARY_CANDIDATE"); self.assertEqual(t15["target_decision_time"], "T-15_ENGINEERING_CANDIDATE"); self.assertNotIn("PRIMARY_FROZEN", repr(result))

    def test_final_json_schema(self):
        temp, _, _, probe = make_probe(datetime.fromisoformat("2026-07-31T10:20:00+00:00"))
        with temp:
            result = probe.run(); path = next((Path(temp.name) / "outputs").rglob("*_live_freshness.json")); stored = json.loads(path.read_text(encoding="utf-8")); self.assertEqual(set(("race", "scheduled_post_time", "run_started_at", "run_finished_at", "captures", "comparison", "overall_status", "warnings")) - set(stored), set()); self.assertEqual(set(stored["captures"]), {"T20", "T15", "T10", "T05"}); self.assertEqual(stored["overall_status"], result["overall_status"])
