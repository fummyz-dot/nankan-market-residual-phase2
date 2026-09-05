import json
import tempfile
import unittest
from pathlib import Path

from src.ingestion.prospective_store import connect, initialize_database, record_capture, register_race
from src.operations.current_info import record_current_snapshot
from src.operations import stabilization_status as status


UTC = "2099-01-02T05:45:00+00:00"
POST = "2099-01-02T06:00:00+00:00"


class CurrentStabilizationMetricsV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "market.sqlite"
        self.v1 = self.root / "v1"
        self.v2 = self.root / "v2"
        self.collection = self.root / "collection"
        initialize_database(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _snapshot(self, *, venue: str = "大井") -> None:
        conn = connect(self.db)
        try:
            registry = register_race(
                conn, race_date="2099-01-02", venue=venue, race_number=1,
                scheduled_post_time=POST, scheduled_post_time_source="TEST",
                scheduled_post_time_captured_at=UTC, eligibility_status="PRIMARY_ELIGIBLE",
            )
            capture = record_capture(
                conn, race_registry_id=registry, source_type="CURRENT_INFO", source_name="TEST",
                source_reference="test", submitted_url="https://example.test", requested_at=UTC,
                captured_at=UTC, source_published_at=None, http_status=200, content_type="text/html",
                encoding="utf-8", raw_archive_path_value="test.html", raw_sha256="a" * 64,
                response_size_bytes=1, capture_status="COLLECTED_OK", commit=False,
            )
            record_current_snapshot(
                conn, race_registry_id=registry, capture_id=capture, mark="T15",
                target_decision_label="T15", scheduled_target_capture_time=UTC, scheduled_post_time=POST,
                captured_at=UTC, source_published_at=None, source_url="test", response_sha256="a" * 64,
                availability="OBSERVED_IN_PREDECISION_RAW_CAPTURE", weather_raw=None, track_condition_raw=None,
                active_runner_count=2, collector_version="test", parser_version="nankan-official-current-card-v1",
                parse_status="PARSED_BODYWEIGHT_JOCKEY_ONLY", capture_status="COMPLETE",
                t15_timing_status="PREDECISION_VALID",
                runners=[
                    {"horse_number": 1, "body_weight": 500, "body_weight_change": 2, "declared_jockey_raw": "raw-present"},
                    {"horse_number": 2, "body_weight": 480, "body_weight_change": None, "declared_jockey_raw": "raw-present"},
                ],
                commit=True,
            )
        finally:
            conn.close()

    @staticmethod
    def _runner(number: int, change: str, *, unknown_reason: str | None = None) -> dict:
        resolved = change in {"SAME", "CHANGED", "NO_PRIOR_START"}
        current_id = None if unknown_reason == "CURRENT_JOCKEY_UNRESOLVED" else "001"
        main_status = "UNKNOWN" if unknown_reason == "MAIN_IDENTITY_UNAVAILABLE" else "RESOLVED"
        prior_status = "UNKNOWN" if unknown_reason == "PRIOR_JOCKEY_OFFICIAL_ID_UNAVAILABLE" else ("NO_PRIOR_START" if change == "NO_PRIOR_START" else "RESOLVED")
        previous_id = "001" if change == "SAME" else "002" if change == "CHANGED" else None
        return {
            "horse_number": number, "jockey_change_status": change,
            "current_jockey_id": current_id, "jockey_source_status": "RESOLVED_OFFICIAL" if current_id else "UNRESOLVED",
            "previous_jockey_id": previous_id, "previous_start_resolution_status": prior_status,
            "previous_start_resolution_reason": unknown_reason or ("NO_NANKAN_ACTUAL_START" if change == "NO_PRIOR_START" else "LAST_NANKAN_ACTUAL_START"),
            "main_horse_identity_status": main_status,
            "main_horse_identity_reason": unknown_reason if main_status == "UNKNOWN" else "IMMUTABLE_MAIN_IDENTITY_AUDIT",
            "current_jockey_change_from_last_nankan_flag": 0 if change == "SAME" else 1 if change == "CHANGED" else None,
        }

    def _v2(self, *, venue: str, race_number: int, runners: list[dict]) -> None:
        race_key = f"2099-01-02_{venue}_{race_number:02d}"
        changes = {item: sum(row["jockey_change_status"] == item for row in runners) for item in ("SAME", "CHANGED", "NO_PRIOR_START", "UNKNOWN")}
        payload = {
            "schema_version": "p2_current_research_payload_v2",
            "jockey_context_version": "P2_CURRENT_JOCKEY_CONTEXT_V2",
            "active_runner_count": len(runners), "runners": runners,
            "jockey_change_counts": changes,
            "current_jockey_resolved_count": sum(row["current_jockey_id"] is not None for row in runners),
            "previous_jockey_resolved_count": sum(row["previous_jockey_id"] is not None for row in runners),
            "reference": {"source_mark": "T15"},
            "current_source": {"raw_source_sha256": "b" * 64},
        }
        envelope = {
            "schema_version": "p2_current_research_evidence_v2", "race_key": race_key,
            "research_bundle_sha256": "c" * 64, "main_bundle_sha256": "d" * 64,
            "status": "CURRENT_RESEARCH_COMMITTED", "payload": payload,
        }
        envelope["payload_sha256"] = status._sha({
            "race_key": race_key, "research_bundle_sha256": envelope["research_bundle_sha256"],
            "main_bundle_sha256": envelope["main_bundle_sha256"], "reference": payload["reference"], "current": payload,
        })
        path = self.v2 / "2099-01-02" / f"{venue}_race{race_number:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    def _v1(self) -> None:
        path = self.v1 / "2099-01-01" / "大井_race01.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": "p2_current_research_evidence_v1", "race_key": "2099-01-01_大井_01",
            "status": "CURRENT_RESEARCH_COMMITTED",
            "payload": {"schema_version": "p2_current_research_payload_v1", "active_runner_count": 1, "runners": [{"horse_number": 1, "declared_jockey_raw": "raw-present"}]},
        }, ensure_ascii=False), encoding="utf-8")

    def _status(self) -> dict:
        return status.build_status(self.db, v1_predictions=self.v1, v2_predictions=self.v2, collection_root=self.collection)

    def test_v1_is_historical_only_and_raw_name_never_supplies_cur03(self) -> None:
        self._snapshot(); self._v1()
        observed = self._status()
        self.assertEqual(observed["current_jockey_context_versions"]["CURRENT_JOCKEY_CONTEXT_V1_HISTORICAL"]["runner_count"], 1)
        self.assertEqual(observed["current_jockey_context_versions"]["CURRENT_JOCKEY_CONTEXT_V1_HISTORICAL"]["v2_stabilization_status"], "NOT_VALID_FOR_V2_STABILIZATION")
        self.assertEqual(observed["cur03_v2"]["evidence_status"], "NOT_YET_OBSERVED")
        self.assertEqual(observed["cur03_v2"]["value_available_count"], 0)
        self.assertIsNone(observed["candidate_field_valid_predecision_coverage"]["CUR03_jockey_change_v2"])

    def test_v2_cur03_counts_and_unknown_reasons_are_exact(self) -> None:
        self._snapshot()
        self._v2(venue="大井", race_number=1, runners=[
            self._runner(1, "SAME"), self._runner(2, "CHANGED"), self._runner(3, "NO_PRIOR_START"),
            self._runner(4, "UNKNOWN", unknown_reason="MAIN_IDENTITY_UNAVAILABLE"),
            self._runner(5, "UNKNOWN", unknown_reason="CURRENT_JOCKEY_UNRESOLVED"),
            self._runner(6, "UNKNOWN", unknown_reason="PRIOR_JOCKEY_OFFICIAL_ID_UNAVAILABLE"),
        ])
        observed = self._status()["cur03_v2"]
        self.assertEqual({key: observed[key] for key in ("SAME", "CHANGED", "NO_PRIOR_START", "UNKNOWN")}, {"SAME": 1, "CHANGED": 1, "NO_PRIOR_START": 1, "UNKNOWN": 3})
        self.assertEqual(observed["value_available_count"], 2)
        self.assertEqual(observed["value_null_by_design_count"], 1)
        self.assertEqual(observed["unresolved_count"], 3)
        self.assertEqual(sum(observed["unknown_reason_counts"].values()), 3)
        self.assertEqual(observed["current_jockey_id_unresolved_count"], 1)
        self.assertEqual(observed["previous_jockey_id_unresolved_count"], 3)

    def test_bodyweight_null_and_failed_checkpoint_are_separate(self) -> None:
        self._snapshot()
        checkpoint = self.collection / "2099-01-02" / "day_collector.run" / "checkpoints" / "r.failed.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps({"error": "ValueError:bodyweight runner count mismatch: 0 != 2"}), encoding="utf-8")
        body = self._status()["current_component_status"]
        self.assertEqual(body["CUR01"]["metrics"]["absolute_bodyweight_resolved_count"], 2)
        self.assertEqual(body["CUR02"]["metrics"]["bodyweight_change_numeric_count"], 1)
        self.assertEqual(body["CUR02"]["metrics"]["bodyweight_change_legitimate_null_count"], 1)
        self.assertEqual(body["CUR02"]["metrics"]["failed_bodyweight_checkpoint_count_not_committed"], 1)
        self.assertFalse(body["CUR02"]["metrics"]["failed_raw_included_in_committed_coverage"])

    def test_v2_venue_observation_only_enables_reaudit_not_h2_start(self) -> None:
        self._snapshot()
        for number, venue in enumerate(("大井", "船橋", "川崎"), start=1):
            self._v2(venue=venue, race_number=number, runners=[self._runner(1, "SAME")])
        observed = self._status()
        self.assertEqual(observed["current_jockey_context_versions"]["P2_CURRENT_JOCKEY_CONTEXT_V2"]["venue_coverage"]["浦和"], "NOT_YET_OBSERVED")
        self.assertEqual(observed["h2_c05_data_gate"]["status"], "NOT_READY")
        self._v2(venue="浦和", race_number=4, runners=[self._runner(1, "CHANGED")])
        observed = self._status()
        self.assertEqual(observed["h2_c05_data_gate"]["status"], "ELIGIBLE_FOR_READINESS_REAUDIT")
        self.assertFalse(observed["h2_c05_data_gate"]["h2_c05_started"])

    def test_malformed_v2_evidence_is_an_integrity_problem_not_coverage(self) -> None:
        self._snapshot()
        bad = self.v2 / "2099-01-02" / "bad.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(json.dumps({"schema_version": "p2_current_research_evidence_v2", "race_key": "2099-01-02_大井_01", "status": "CURRENT_RESEARCH_COMMITTED", "payload": {}}), encoding="utf-8")
        observed = self._status()
        self.assertEqual(observed["current_jockey_context_versions"]["P2_CURRENT_JOCKEY_CONTEXT_V2"]["invalid_evidence_file_count"], 1)
        self.assertEqual(observed["cur03_v2"]["evidence_status"], "INTEGRITY_PROBLEM")


if __name__ == "__main__":
    unittest.main()
