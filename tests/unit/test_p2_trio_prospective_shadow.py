"""TRIO V0 prospective boundaries, integrity, replay, and outcome tests."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

from src.operations import trio_research_evaluation as evaluation
from src.operations import trio_research_shadow as trio
from src.operations.live_development_store import connect, initialize_database, transaction
from src.operations.wide_ops_v0 import exact_pl_trio_probabilities


UTC = timezone.utc
DATE, VENUE = "2099-06-01", "船橋"
FS04 = json.loads(Path("data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json").read_text(encoding="utf-8"))["ordered_feature_names"]


def _race(number: int, post: datetime, *, date: str = DATE) -> dict:
    return {"race_key": f"P2_RACE_V1::{date}\x1f{VENUE}\x1f{number}", "race_date": date, "venue": VENUE, "race_number": number, "scheduled_post_time": post.isoformat()}


def _reference(now: datetime, post: datetime, *, fallback: bool = False) -> dict:
    mark = "RECOVERY" if fallback else "T15"
    return {
        "mode": "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD", "source_mark": mark,
        "scientific_sample": not fallback, "market_capture_id": f"win-{mark}", "current_capture_id": f"current-{mark}",
        "current_snapshot_id": f"current-snapshot-{mark}", "market_snapshot_id": f"win-{mark}",
        "wide_capture_id": f"wide-{mark}", "wide_capture_status": "COMPLETE", "trio_capture_id": f"trio-{mark}", "trio_capture_status": "COMPLETE",
        "market_captured_at": now.isoformat(), "current_captured_at": now.isoformat(), "scheduled_post_time": post.isoformat(),
        "seconds_to_post_at_reference": (post - now).total_seconds(),
        "market_snapshot_sha256": "a" * 64, "wide_snapshot_sha256": "b" * 64, "trio_snapshot_sha256": "c" * 64, "current_snapshot_sha256": "d" * 64,
    }


def _main(race: dict, reference: dict, numbers: list[int]) -> dict:
    return {"race": copy.deepcopy(race), "predecision_reference": copy.deepcopy(reference), "dev_live_v1": {"candidate": [{"horse_number": number, "candidate_probability": 1.0 / len(numbers)} for number in numbers]}}


def _materialized(race: dict, reference: dict, numbers: list[int], *, complete: bool = True, duplicate: bool = False, invalid_odds: bool = False) -> dict:
    rows = [{"horse_number": number, **{name: float(number) for name in FS04}} for number in numbers]
    wide_rows = []
    for first, second in combinations(numbers, 2):
        lower = 3.0 + first + second / 10.0
        wide_rows.append({"horse_number_1": first, "horse_number_2": second, "lower_odds": lower, "upper_odds": lower + 1.0, "notes": json.dumps({"lower_odds_raw": f"{lower:.1f}"})})
    trio_rows = []
    for first, second, third in combinations(numbers, 3):
        odds = 30.0 + first + second + third
        trio_rows.append({"horse_number_1": first, "horse_number_2": second, "horse_number_3": third, "odds_value": 0.0 if invalid_odds and not trio_rows else odds})
    if not complete:
        trio_rows.pop()
    if duplicate:
        trio_rows.append(copy.deepcopy(trio_rows[0]))
    return {"identity": copy.deepcopy(race), "predecision_reference": copy.deepcopy(reference), "feature_names": FS04, "rows": rows, "primary_eligibility": {"status": "PRIMARY_ELIGIBLE"}, "t15_snapshot_parent": {"t15_wide_rows": wide_rows, "t15_trio_rows": trio_rows}}


def _wide_payload(reference: dict, numbers: list[int]) -> dict:
    subsets = list(combinations(numbers, 3)); mass = 1.0 / len(subsets)
    return {
        "schema_version": "p2_wide_research_prediction_v1", "status": "COMMITTED",
        "models": {"j0_model_id": trio.wide.J0_ID, "j1_model_id": trio.wide.J1_ID},
        "reference": {key: reference.get(key) for key in (
            "mode", "source_mark", "market_capture_id", "current_capture_id", "market_snapshot_id", "market_captured_at",
            "current_captured_at", "scheduled_post_time", "seconds_to_post_at_reference", "wide_capture_id",
            "market_snapshot_sha256", "wide_snapshot_sha256", "current_snapshot_sha256",
        )},
        "active_runner_count": len(numbers), "ordered_top3_subset_count": len(subsets),
        "subsets": [{"horse_numbers": list(values), "p_j0": mass, "p_j1": mass} for values in subsets],
        "result_db_accessed": 0,
    }


def _insert_wide_evidence(db: Path, race: dict, reference: dict, numbers: list[int], frozen: dict, main_bundle_sha256: str, *, payload: dict | None = None, model_bundle_sha256: str | None = None, status: str = "RESEARCH_WIDE_COMMITTED") -> None:
    value = payload or _wide_payload(reference, numbers)
    bundle = model_bundle_sha256 or frozen["wide_joint_bundle_sha256"]
    try:
        canonical = trio._canonical(value).decode("utf-8")
        evidence_canonical = {"race_key": race["race_key"], "model_bundle_sha256": bundle, "main_bundle_sha256": main_bundle_sha256, "reference": value["reference"], "prediction": value}
        evidence_digest = trio._sha(trio._canonical(evidence_canonical))
        identifier = trio.wide.RESEARCH_ID_PREFIX + evidence_digest
    except ValueError:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True)
        evidence_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identifier = "P2_WIDE_RESEARCH_V1::INVALID"
    conn = connect(db)
    try:
        with transaction(conn):
            conn.execute(
                """INSERT INTO wide_research_evidence(
                    research_prediction_id,race_key,created_at,reference_mode,source_mark,market_snapshot_id,current_snapshot_id,captured_at,scheduled_post_time,
                    model_bundle_sha256,market_model_id,market_gamma,j0_model_id,j1_model_id,pl_model_id,confirmation_scope,status,payload_json,payload_sha256,main_bundle_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, race["race_key"], reference["market_captured_at"], reference["mode"], reference["source_mark"], reference["market_snapshot_id"], reference["current_snapshot_id"], reference["market_captured_at"], reference["scheduled_post_time"], bundle, "M0", 1.0, trio.wide.J0_ID, trio.wide.J1_ID, "PL", "PRIMARY_T15", status, canonical, evidence_digest, main_bundle_sha256),
            )
    finally:
        conn.close()


def _insert_race(db: Path, race: dict, now: datetime) -> None:
    initialize_database(db); conn = connect(db)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO race_registry VALUES(?,?,?,?,?,?,?)", (race["race_key"], race["race_date"], race["venue"], race["race_number"], race["scheduled_post_time"], "official://card", now.isoformat()))
    finally:
        conn.close()


def _insert_result(db: Path, race: dict, raw_root: Path, *, cancelled: bool = False, dead_heat: bool = False) -> None:
    raw = raw_root / "result.html"; raw.write_text("<html><body>official result</body></html>", encoding="utf-8")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest(); capture = "RESULT::" + str(race["race_number"])
    conn = connect(db)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (capture, race["race_key"], "official://result", "2099-06-01T12:00:00+00:00", 200, "text/html", str(raw), digest, raw.stat().st_size, "RESULT_OFFICIAL_FINAL", "test", "PARSED", "2099-06-01T12:00:00+00:00"))
            for number in range(1, 6):
                status = "取消" if cancelled and number == 5 else str(number)
                conn.execute("INSERT INTO official_runner_results VALUES(?,?,?,?,?,?,?)", (capture, race["race_key"], number, None if status == "取消" else number, None if status == "取消" else "FINISHED", status, "PARSED"))
            sets = [(1, 2, 3), (1, 2, 4)] if dead_heat else [(1, 2, 3)]
            for order, values in enumerate(sets, 1):
                text = "-".join(map(str, values))
                conn.execute("INSERT INTO official_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f"PAYOUT::{race['race_number']}::{order}", capture, race["race_key"], "TRIO", text, text, "100", 100, "YEN_PER_100", order, "PARSED"))
    finally:
        conn.close()


class TrioProspectiveShadowTest(unittest.TestCase):
    def _frozen(self, root: Path) -> dict:
        return trio.freeze_bundle(confirmation_start="2099-01-01T00:00:00+00:00", bundle_dir=root / "bundle")

    def test_complete_five_runner_and_large_pl_probability_integrity(self) -> None:
        now, post = datetime(2099, 6, 1, 9, tzinfo=UTC), datetime(2099, 6, 1, 9, 15, tzinfo=UTC)
        race, ref, numbers = _race(5, post), _reference(now, post), [1, 2, 3, 4, 5]
        with tempfile.TemporaryDirectory() as temporary:
            frozen = self._frozen(Path(temporary)); payload = trio.build_prediction(main_bundle=_main(race, ref, numbers), materialized=_materialized(race, ref, numbers), frozen=frozen, wide_payload=_wide_payload(ref, numbers))
        self.assertEqual((payload["expected_trio_count"], payload["actual_trio_count"]), (10, 10))
        self.assertEqual(set(payload["unordered_probability_sums"]), {"tm0_probability", "tj0_probability", "tj1_probability", "tpl_probability"})
        self.assertTrue(all(abs(value - 1.0) < 1e-9 for value in payload["unordered_probability_sums"].values()))
        self.assertTrue(all(not ticket["recommended"] and ticket["stake_yen"] == 0 for ticket in payload["tickets"]))
        for size in (11, 14):
            output = exact_pl_trio_probabilities({"horse_number": number, "candidate_probability": 1.0 / size} for number in range(1, size + 1))
            self.assertEqual(output["expected_trio_count"], math_comb(size, 3))
            self.assertAlmostEqual(output["trio_mass_sum"], 1.0)
            self.assertTrue(all(item["ordered_permutation_count"] == 6 for item in output["trios"]))

    def test_incomplete_duplicate_and_invalid_odds_fail_closed(self) -> None:
        now, post = datetime(2099, 6, 1, 9, tzinfo=UTC), datetime(2099, 6, 1, 9, 15, tzinfo=UTC)
        race, ref, numbers = _race(6, post), _reference(now, post), [1, 2, 3, 4, 5]
        with tempfile.TemporaryDirectory() as temporary:
            frozen = self._frozen(Path(temporary))
            for kwargs, code in (({"complete": False}, "TRIO_MARKET_INCOMPLETE"), ({"duplicate": True}, "TRIO_MARKET_DUPLICATE_SET"), ({"invalid_odds": True}, "TRIO_MARKET_INVALID_ODDS")):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaisesRegex(trio.TrioResearchError, code):
                        trio.build_prediction(main_bundle=_main(race, ref, numbers), materialized=_materialized(race, ref, numbers, **kwargs), frozen=frozen, wide_payload=_wide_payload(ref, numbers))

    def test_committed_wide_evidence_is_exact_source_and_never_recomputed(self) -> None:
        now, post = datetime(2099, 6, 1, 9, tzinfo=UTC), datetime(2099, 6, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "live.sqlite"; frozen = self._frozen(root)
            race, ref, numbers = _race(16, post), _reference(now, post), [1, 2, 3, 4, 5]
            main = {"bundle": _main(race, ref, numbers), "bundle_sha256": "z" * 64, "committed_at": now.isoformat()}
            _insert_race(db, race, now); _insert_wide_evidence(db, race, ref, numbers, frozen, main["bundle_sha256"])
            materialized = _materialized(race, ref, numbers)
            loaded = trio._load_committed_wide_payload(evidence_db=db, race=race, main_bundle_sha256=main["bundle_sha256"], main_bundle=main["bundle"], materialized=materialized, frozen=frozen)
            self.assertEqual(loaded, _wide_payload(ref, numbers))
            with patch.object(trio, "lookup_existing_recommendation", return_value=main), patch.object(trio, "OUT", root / "out"), patch.object(trio.wide, "build_prediction", side_effect=AssertionError("must not recompute")):
                value = trio.run(race_date=DATE, venue=VENUE, race_number=16, evidence_db=db, now=now, now_fn=lambda: now, materializer=lambda **_: materialized, bundle_dir=root / "bundle")
            self.assertEqual(value["status"], trio.STATUS_COMMITTED)
            self.assertEqual(value["result_db_accessed"], 0)

    def test_wide_evidence_dependency_failures_are_specific_and_fail_closed(self) -> None:
        now, post = datetime(2099, 6, 1, 9, tzinfo=UTC), datetime(2099, 6, 1, 9, 15, tzinfo=UTC)
        cases = (
            ("missing", None, "TRIO_WIDE_RESEARCH_EVIDENCE_MISSING"),
            ("bundle", {"model_bundle_sha256": "x" * 64}, "TRIO_WIDE_RESEARCH_EVIDENCE_BUNDLE_MISMATCH"),
            ("main", {"main_bundle_sha256": "x" * 64}, "TRIO_WIDE_RESEARCH_EVIDENCE_PROVENANCE_MISMATCH"),
            ("reference", {"payload_mutation": lambda payload: payload["reference"].__setitem__("wide_capture_id", "wrong")}, "TRIO_WIDE_RESEARCH_EVIDENCE_REFERENCE_MISMATCH"),
            ("duplicate", {"payload_mutation": lambda payload: payload["subsets"].append(copy.deepcopy(payload["subsets"][0]))}, "TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_INVALID"),
            ("missing_subset", {"payload_mutation": lambda payload: payload["subsets"].pop()}, "TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_ROSTER_MISMATCH"),
            ("nonfinite", {"payload_mutation": lambda payload: payload["subsets"][0].__setitem__("p_j0", float("nan"))}, "TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_INVALID"),
        )
        for label, mutation, code in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); db = root / "live.sqlite"; frozen = self._frozen(root)
                race, ref, numbers = _race(17, post), _reference(now, post), [1, 2, 3, 4, 5]
                main = _main(race, ref, numbers); materialized = _materialized(race, ref, numbers)
                _insert_race(db, race, now)
                if label != "missing":
                    payload = _wide_payload(ref, numbers)
                    if mutation and mutation.get("payload_mutation"):
                        mutation["payload_mutation"](payload)
                    _insert_wide_evidence(db, race, ref, numbers, frozen, "m" * 64 if mutation.get("main_bundle_sha256") is None else mutation["main_bundle_sha256"], payload=payload, model_bundle_sha256=mutation.get("model_bundle_sha256"))
                with self.assertRaisesRegex(trio.TrioResearchError, code):
                    trio._load_committed_wide_payload(evidence_db=db, race=race, main_bundle_sha256="m" * 64, main_bundle=main, materialized=materialized, frozen=frozen)

    def test_replay_fallback_and_engineering_exclusion_do_not_mutate_main(self) -> None:
        now, post = datetime(2099, 6, 1, 9, tzinfo=UTC), datetime(2099, 6, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "live.sqlite"; frozen = self._frozen(root)
            race, ref, numbers = _race(7, post), _reference(now, post, fallback=True), [1, 2, 3, 4, 5]
            _insert_race(db, race, now); main = {"bundle": _main(race, ref, numbers), "bundle_sha256": "m" * 64, "committed_at": now.isoformat()}; original = copy.deepcopy(main)
            _insert_wide_evidence(db, race, ref, numbers, frozen, main["bundle_sha256"])
            with patch.object(trio, "lookup_existing_recommendation", return_value=main), patch.object(trio, "OUT", root / "out"):
                first = trio.run(race_date=DATE, venue=VENUE, race_number=7, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: _materialized(race, ref, numbers), bundle_dir=root / "bundle")
                second = trio.run(race_date=DATE, venue=VENUE, race_number=7, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: self.fail("must not rebuild"), bundle_dir=root / "bundle")
            self.assertEqual((first["status"], second["status"]), (trio.STATUS_COMMITTED, trio.STATUS_IDEMPOTENT))
            self.assertEqual(first["confirmation_scope"], "SECONDARY_FALLBACK")
            self.assertFalse(first["confirmation_eligible"]); self.assertEqual(first["result_db_accessed"], 0); self.assertEqual(main, original)
            old_race = _race(8, post, date="2026-08-28"); old_ref = _reference(datetime(2026, 8, 28, 11, 34, tzinfo=UTC), datetime(2026, 8, 28, 11, 50, tzinfo=UTC))
            old_payload = trio.build_prediction(main_bundle=_main(old_race, old_ref, numbers), materialized=_materialized(old_race, old_ref, numbers), frozen=frozen, wide_payload=_wide_payload(old_ref, numbers))
            self.assertEqual(trio._confirmation(old_payload["reference"], old_race, old_ref["market_captured_at"], frozen)[2], "PROSPECTIVE_CONFIRMATION_EXCLUDED")
            self.assertEqual(
                trio._confirmation(ref, race, now.isoformat(), frozen, primary_race_eligible=False)[2],
                "NOT_P2_PRIMARY_RACE",
            )

    def test_same_logical_key_with_different_immutable_payload_conflicts_without_overwrite(self) -> None:
        now, post = datetime(2099, 6, 1, 9, tzinfo=UTC), datetime(2099, 6, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "live.sqlite"; frozen = self._frozen(root); race, ref, numbers = _race(11, post), _reference(now, post), [1, 2, 3, 4, 5]
            _insert_race(db, race, now); payload = trio.build_prediction(main_bundle=_main(race, ref, numbers), materialized=_materialized(race, ref, numbers), frozen=frozen, wide_payload=_wide_payload(ref, numbers))
            with patch.object(trio, "OUT", root / "out"):
                first = trio._commit_prediction(evidence_db=db, race=race, main_bundle_sha256="q" * 64, main_committed_at=now.isoformat(), frozen=frozen, payload=payload, created_at=now)
                changed = copy.deepcopy(payload); changed["tickets"][0]["official_odds"] += 1.0
                with self.assertRaisesRegex(trio.TrioResearchError, "TRIO_RESEARCH_ALREADY_COMMITTED_DIFFERENT"):
                    trio._commit_prediction(evidence_db=db, race=race, main_bundle_sha256="q" * 64, main_committed_at=now.isoformat(), frozen=frozen, payload=changed, created_at=now)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*),payload_sha256 FROM trio_research_evidence").fetchone()[0], 1)
                self.assertEqual(first["status"], trio.STATUS_COMMITTED)
            finally:
                conn.close()

    def test_outcome_dead_heat_and_post_reference_withdrawal(self) -> None:
        now, post = datetime(2099, 6, 1, 9, tzinfo=UTC), datetime(2099, 6, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "live.sqlite"; frozen = self._frozen(root); race, ref, numbers = _race(9, post), _reference(now, post), [1, 2, 3, 4, 5]
            _insert_race(db, race, now); main = {"bundle": _main(race, ref, numbers), "bundle_sha256": "n" * 64, "committed_at": now.isoformat()}
            _insert_wide_evidence(db, race, ref, numbers, frozen, main["bundle_sha256"])
            with patch.object(trio, "lookup_existing_recommendation", return_value=main), patch.object(trio, "OUT", root / "out"), patch.object(evaluation, "OUT", root / "out"):
                self.assertEqual(trio.run(race_date=DATE, venue=VENUE, race_number=9, evidence_db=db, now=now, now_fn=lambda: now, materializer=lambda **_: _materialized(race, ref, numbers), bundle_dir=root / "bundle")["status"], trio.STATUS_COMMITTED)
                _insert_result(db, race, root, dead_heat=True)
                evaluated = evaluation.evaluate_day(date=DATE, venue=VENUE, races=[9], evidence_db=db)
                repeat = evaluation.evaluate_day(date=DATE, venue=VENUE, races=[9], evidence_db=db)
            self.assertEqual(evaluated["outcomes"][0]["status"], "TRIO_RESEARCH_EVALUATED")
            self.assertEqual(len(evaluated["outcomes"][0]["metrics"]["winning_sets"]), 2)
            self.assertEqual(repeat["outcomes"][0]["status"], "TRIO_RESEARCH_EVALUATION_IDEMPOTENT")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "live.sqlite"; frozen = self._frozen(root); race, ref, numbers = _race(10, post), _reference(now, post), [1, 2, 3, 4, 5]
            _insert_race(db, race, now); main = {"bundle": _main(race, ref, numbers), "bundle_sha256": "o" * 64, "committed_at": now.isoformat()}
            _insert_wide_evidence(db, race, ref, numbers, frozen, main["bundle_sha256"])
            with patch.object(trio, "lookup_existing_recommendation", return_value=main), patch.object(trio, "OUT", root / "out"), patch.object(evaluation, "OUT", root / "out"):
                trio.run(race_date=DATE, venue=VENUE, race_number=10, evidence_db=db, now=now, now_fn=lambda: now, materializer=lambda **_: _materialized(race, ref, numbers), bundle_dir=root / "bundle")
                _insert_result(db, race, root, cancelled=True)
                excluded = evaluation.evaluate_day(date=DATE, venue=VENUE, races=[10], evidence_db=db)
            self.assertEqual(excluded["outcomes"][0]["status"], "POST_REFERENCE_WITHDRAWAL")


def math_comb(n: int, r: int) -> int:
    return n * (n - 1) * (n - 2) // 6


if __name__ == "__main__":
    unittest.main()
