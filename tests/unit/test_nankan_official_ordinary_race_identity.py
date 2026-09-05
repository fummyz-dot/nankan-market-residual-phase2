import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.operations.live_freshness_probe import LiveFreshnessProbe, ProbeConfig, resolve_bootstrap_response

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/nankan_official/ordinary_conditions_race.html"
URL = "https://www.nankankeiba.com/uma_shosai/2026081921060205.do"


class FixedClock:
    def __init__(self): self.value = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)
    def now(self): return self.value
    def monotonic(self): return 0.0
    def sleep(self, seconds): raise AssertionError("TOO_EARLY_TO_START must not sleep")


class OneResponseFetcher:
    def __init__(self, response): self.response, self.calls = response, 0
    def fetch(self, url, timeout_seconds): self.calls += 1; return self.response


class OrdinaryRaceIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_bytes()
        cls.response = official.FetchResult(
            requested_url=URL, request_started_at="2026-08-19T07:00:00+00:00",
            captured_at="2026-08-19T07:00:01+00:00", final_url=URL,
            redirect_chain=[], status_code=200, headers={"Content-Type": "text/html; charset=utf-8"}, raw=cls.raw,
        )

    def test_ordinary_race_has_nullable_name_and_separate_conditions(self):
        identity = official.resolve_race(URL, official.decode_html(self.raw, "text/html; charset=utf-8"))
        self.assertEqual(identity, {
            "race_date": "2026-08-19", "venue": "川崎", "race_number": 5,
            "race_name": None, "conditions_raw": "Ｃ２(三)(四)",
            "scheduled_post_time_local": "16:45", "distance_m": 1400,
            "surface": "ダート", "field_size": 11,
        })

    def test_bootstrap_response_resolves_identity_without_another_fetch(self):
        identity, post = resolve_bootstrap_response(URL, self.response)
        self.assertIsNone(identity["race_name"])
        self.assertEqual(identity["conditions_raw"], "Ｃ２(三)(四)")
        self.assertEqual(post.isoformat(), "2026-08-19T07:45:00+00:00")

    def test_live_probe_bootstrap_reaches_identity_before_bounded_early_exit(self):
        clock, fetcher = FixedClock(), OneResponseFetcher(self.response)
        with tempfile.TemporaryDirectory() as temporary:
            probe = LiveFreshnessProbe(
                ProbeConfig(URL, db_path=Path(temporary) / "probe.sqlite", output_root=Path(temporary) / "outputs", max_initial_wait_seconds=45 * 60),
                clock=clock, fetcher=fetcher, printer=None,
            )
            result = probe.run()
        self.assertEqual(result["overall_status"], "TOO_EARLY_TO_START")
        self.assertEqual(result["race"]["race_number"], 5)
        self.assertIsNone(result["race"]["race_name"])
        self.assertEqual(fetcher.calls, 1)

    def test_fixture_is_not_a_market_snapshot(self):
        self.assertNotIn("odds", FIXTURE.read_text(encoding="utf-8").casefold())


if __name__ == "__main__":
    unittest.main()
