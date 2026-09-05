from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.operations import current_research_shadow as current


class CurrentPreviousJockeyIdentityContractV1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "base.sqlite"
        self.delta = self.root / "delta.sqlite"
        for path, delta in ((self.base, False), (self.delta, True)):
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE races(race_key TEXT PRIMARY KEY,race_date TEXT,venue TEXT,race_number INTEGER);
                CREATE TABLE race_runners(race_key TEXT,horse_identity_key TEXT,horse_number INTEGER,jockey TEXT,result_status TEXT,margin_raw TEXT,finish_position INTEGER);
            """)
            if delta:
                conn.execute("CREATE TABLE v1_person_category_context(race_key TEXT,horse_number INTEGER,jockey_official_id TEXT,jockey_raw_display TEXT,jockey_registered_name TEXT)")
            conn.commit()
            conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _insert(self, path: Path, *, key: str, day: str, venue: str = "船橋", number: int = 1,
                horse: str = "H", status: str = "FINISHED", margin: str | None = None,
                finish: int | None = 1, raw: str = "旧騎手", jockey_id: str | None = None) -> None:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO races VALUES(?,?,?,?)", (key, day, venue, number))
        conn.execute("INSERT INTO race_runners VALUES(?,?,?,?,?,?,?)", (key, horse, 1, raw, status, margin, finish))
        if jockey_id is not None:
            conn.execute("INSERT INTO v1_person_category_context VALUES(?,?,?,?,?)", (key, 1, jockey_id, raw, raw))
        conn.commit()
        conn.close()

    def _main(self, audit: list[dict] | None = None) -> dict:
        race = {"race_key": "P2_RACE_V1::2099-01-02\x1f船橋\x1f6", "race_date": "2099-01-02", "venue": "船橋", "race_number": 6,
                "scheduled_post_time": "2099-01-02T06:00:00+00:00"}
        return {"mode": "LIVE_SHADOW", "source_boundary": {"result_db_accessed": 0, "result_fields_present": False, "payout_fields_present": False},
                "race": race, "predecision_reference": {"mode": "T15_STANDARD", "source_mark": "T15", "current_capture_id": "cap", "current_snapshot_id": "snap", "current_captured_at": "2099-01-02T05:40:00+00:00", "scheduled_post_time": race["scheduled_post_time"], "seconds_to_post_at_reference": 1200.0},
                "active_roster": [{"horse_number": 1, "horse_name_exact": "馬一"}],
                "main_identity_audit": {"schema_version": "p2_main_runner_identity_audit_v1", "race_key": race["race_key"], "runners": audit if audit is not None else [{"horse_number": 1, "identity_status": "RESOLVED", "horse_identity_key": "H", "birth_date": "2020-01-01"}]}}

    def _source(self, *, birth_date: str | None = None, current_id: str | None = "101") -> dict:
        raw = "現在騎手" if current_id else None
        return {"snapshot": {"capture_id": "cap", "current_snapshot_id": "snap", "captured_at": "2099-01-02T05:40:00+00:00"},
                "runner_rows": [{"horse_number": 1, "horse_name_exact": "馬一", "birth_date": birth_date, "body_weight_kg": 500, "body_weight_change_kg": 0, "declared_jockey_raw": raw}],
                "statuses": {1: {"normalized_status": "ACTIVE"}},
                "jockey_identities": {1: {"declared_jockey_id": current_id, "declared_jockey_raw": raw, "jockey_source_status": "RESOLVED_OFFICIAL" if current_id else "UNRESOLVED"}}, "raw_sha256": "a" * 64}

    def test_main_identity_replaces_current_birth_date_and_ids_drive_same_changed(self) -> None:
        self._insert(self.delta, key="D1", day="2099-01-01", jockey_id="101")
        payload, _ = current.build_current_payload(main_bundle=self._main(), source=self._source(birth_date=None), base_history=self.base, delta_history=self.delta)
        row = payload["runners"][0]
        self.assertEqual(row["main_horse_identity_key"], "H")
        self.assertEqual(row["jockey_change_status"], "SAME")
        self.assertEqual(row["current_jockey_change_from_last_nankan_flag"], 0)
        self.assertEqual(payload["completeness_state"], "COMPLETE")

    def test_missing_or_ambiguous_main_identity_is_unknown_without_fallback(self) -> None:
        for audit in ([], [{"horse_number": 1, "identity_status": "RESOLVED", "horse_identity_key": "A"}, {"horse_number": 1, "identity_status": "RESOLVED", "horse_identity_key": "B"}]):
            payload, _ = current.build_current_payload(main_bundle=self._main(audit), source=self._source(), base_history=self.base, delta_history=self.delta)
            row = payload["runners"][0]
            self.assertEqual(row["jockey_change_status"], "UNKNOWN")
            self.assertIsNone(row["current_jockey_change_from_last_nankan_flag"])
            self.assertIsNone(row["previous_jockey_id"])

    def test_latest_nankan_actual_start_deduplicates_and_prefers_delta_id(self) -> None:
        self._insert(self.base, key="R1", day="2099-01-01", raw="base")
        self._insert(self.delta, key="R1", day="2099-01-01", raw="delta", jockey_id="101")
        prior = current._prior_start(horse_identity_key="H", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(prior["previous_race_key"], "R1")
        self.assertEqual(prior["previous_jockey_id"], "101")
        self.assertEqual(prior["previous_jockey_raw"], "delta")

    def test_selection_precedes_source_preference_and_base_raw_never_compares(self) -> None:
        self._insert(self.delta, key="OLD_DELTA", day="2098-12-31", jockey_id="101")
        self._insert(self.base, key="NEW_BASE", day="2099-01-01", raw="101")
        prior = current._prior_start(horse_identity_key="H", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(prior["previous_race_key"], "NEW_BASE")
        self.assertEqual(prior["status"], "UNKNOWN")
        self.assertEqual(prior["reason"], "PRIOR_JOCKEY_OFFICIAL_ID_UNAVAILABLE")

    def test_nonstarter_is_excluded_but_unclassified_newer_status_fails_safe(self) -> None:
        self._insert(self.delta, key="ACTUAL", day="2098-12-31", jockey_id="101")
        self._insert(self.delta, key="CANCELLED", day="2099-01-01", status="RAW_FINISH_STATUS_MISSING", margin="出走取消", finish=None, jockey_id="999")
        prior = current._prior_start(horse_identity_key="H", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(prior["previous_race_key"], "ACTUAL")
        self._insert(self.delta, key="AMBIG", day="2099-01-01", number=2, status="RAW_FINISH_STATUS_MISSING", margin="未分類", finish=None, jockey_id="999")
        prior = current._prior_start(horse_identity_key="H", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(prior["status"], "UNKNOWN")
        self.assertEqual(prior["reason"], "PRIOR_START_STATUS_UNCLASSIFIED")

    def test_no_prior_and_current_jockey_missing_never_produce_flag(self) -> None:
        prior = current._prior_start(horse_identity_key="H", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(prior["status"], "NO_PRIOR_START")
        payload, _ = current.build_current_payload(main_bundle=self._main(), source=self._source(current_id=None), base_history=self.base, delta_history=self.delta)
        row = payload["runners"][0]
        self.assertEqual(row["jockey_change_status"], "NO_PRIOR_START")
        self.assertIsNone(row["current_jockey_change_from_last_nankan_flag"])

    def test_cross_venue_is_ignored_and_strict_boundary_is_audited(self) -> None:
        self._insert(self.delta, key="OUTSIDE", day="2099-01-01", venue="園田", jockey_id="999")
        self._insert(self.delta, key="NANKAN", day="2098-12-31", venue="川崎", jockey_id="101")
        prior = current._prior_start(horse_identity_key="H", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(prior["previous_race_key"], "NANKAN")
        self._insert(self.delta, key="SAME_DAY", day="2099-01-02", jockey_id="101")
        self._insert(self.delta, key="FUTURE", day="2099-01-03", jockey_id="101")
        audited = current._prior_start(horse_identity_key="H", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(audited["audit"]["same_day_rows_visible"], 1)
        self.assertEqual(audited["audit"]["future_rows_visible"], 1)
        with self.assertRaisesRegex(current.CurrentResearchError, "CURRENT_RESEARCH_HISTORY_BOUNDARY_VIOLATION"):
            current.build_current_payload(main_bundle=self._main(), source=self._source(), base_history=self.base, delta_history=self.delta)

    def test_unknown_previous_is_context_only_and_does_not_make_current_incomplete(self) -> None:
        self._insert(self.base, key="BASE_ONLY", day="2099-01-01", raw="same-text-as-current")
        payload, _ = current.build_current_payload(main_bundle=self._main(), source=self._source(), base_history=self.base, delta_history=self.delta)
        row = payload["runners"][0]
        self.assertEqual(row["jockey_change_status"], "UNKNOWN")
        self.assertIsNone(row["current_jockey_change_from_last_nankan_flag"])
        self.assertEqual(payload["completeness_state"], "COMPLETE")
        self.assertEqual(payload["schema_version"], "p2_current_research_payload_v2")


if __name__ == "__main__":
    unittest.main()
