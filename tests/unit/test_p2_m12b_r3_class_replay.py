"""Regression coverage for P2-M12B-R3's chronological Class replay harness."""

from __future__ import annotations

import copy
import random
import unittest

from src.audit.p2_m12b_online_v1_parity import FIXTURE_RACES
from src.features.online import class_features as online
from src.audit import p2_m03a_empirical_rating_protocol as rating


def _key(row: dict) -> tuple[str, str, str]:
    return (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))


class P2M12BR3ClassReplayTests(unittest.TestCase):
    """These use the fixed failed fixture set; no result/payout database is read."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.targets = online.historical_fixture_class_targets(set(FIXTURE_RACES))
        cls.by_race = {target["race_key"]: target for target in cls.targets}
        cls.sequential = online.build_online_class_features(cls.targets)

    def _rows_for(self, race_key: str, rows: list[dict] | None = None) -> list[dict]:
        return [row for row in (self.sequential if rows is None else rows) if row["race_key"] == race_key]

    def test_class_feature_count_24(self) -> None:
        self.assertEqual(len(online.CLASS_FIELDS), 24)
        self.assertTrue(all(set(online.CLASS_FIELDS) <= set(row) for row in self.sequential))

    def test_previous_failed_fixture_set_now_exact(self) -> None:
        from src.audit.p2_m12b_online_class_parity import _reference

        keys = {_key(row) for row in self.sequential}
        reference = _reference(keys)
        for row in self.sequential:
            expected = reference[_key(row)]
            for name in online.CLASS_FIELDS:
                actual = row[name]
                if actual is None or expected[name] == "":
                    self.assertEqual(actual is None, expected[name] == "")
                elif name in {"ruleset_id", "class_top_code", "class_bottom_code", "race_taxonomy_code", "race_grade_code", "official_class_direction", "context_fallback_level"}:
                    self.assertEqual(str(actual), expected[name])
                else:
                    self.assertLessEqual(abs(float(actual) - float(expected[name])), 1e-12)

    def test_fixture_input_order_invariant(self) -> None:
        reversed_rows = online.build_online_class_features(list(reversed(self.targets)))
        shuffled = list(self.targets)
        random.Random(20260820).shuffle(shuffled)
        shuffled_rows = online.build_online_class_features(shuffled)
        self.assertEqual(self.sequential, reversed_rows)
        self.assertEqual(self.sequential, shuffled_rows)

    def test_sequential_replay_equals_single_target_rebuild(self) -> None:
        for target in self.targets[:3]:
            standalone = online.build_online_class_features([target])
            self.assertEqual(self._rows_for(target["race_key"]), standalone)

    def test_later_fixture_sees_intervening_prior_updates(self) -> None:
        """Removing prior dates changes the later fixture; the production replay retains them."""
        later = self.targets[-1]
        dates = rating.load_nankan_races(rating.load_class_rows())
        pruned = {date: rows for date, rows in dates.items() if date >= later["race_date"]}
        without_prior = online.build_online_class_features_with_fixture_state(
            [later], dates=pruned, class_rows=rating.load_class_rows()
        )
        self.assertNotEqual(self._rows_for(later["race_key"]), without_prior)

    def test_fixture_does_not_see_own_date_results(self) -> None:
        target = self.targets[1]
        dates = rating.load_nankan_races(rating.load_class_rows())
        mutated = copy.deepcopy(dates)
        for race in mutated[target["race_date"]]:
            for runner in race["runners"]:
                runner["finish_position"] = 99 - int(runner["horse_number"])
        rebuilt = online.build_online_class_features_with_fixture_state(
            [target], dates=mutated, class_rows=rating.load_class_rows()
        )
        self.assertEqual(self._rows_for(target["race_key"]), rebuilt)

    def test_next_day_fixture_sees_previous_day_updates(self) -> None:
        # The first and second fixed targets are separated by ordinary prior
        # dates; standalone reconstruction must equal the chronological state.
        later = self.targets[1]
        self.assertEqual(self._rows_for(later["race_key"]), online.build_online_class_features([later]))

    def test_multiple_same_day_fixtures_share_same_pre_date_state(self) -> None:
        # Use two real races from one date when available; all date-block outputs
        # are invariant to their ordering because updates occur after emission.
        dates = rating.load_nankan_races(rating.load_class_rows())
        same_day = next(rows for rows in dates.values() if len(rows) >= 2)
        selected = same_day[:2]
        targets = [
            {**{key: race[key] for key in ("race_key", "race_date", "venue", "race_number", "field_size")},
             "class_row": race["class_row"],
             "runners": [{"horse_identity_key": item["horse_identity_key"], "horse_number": item["horse_number"]} for item in race["runners"]]}
            for race in selected
        ]
        self.assertEqual(online.build_online_class_features(targets), online.build_online_class_features(list(reversed(targets))))

    def test_context_prior_advances_between_fixture_dates(self) -> None:
        first, last = self.targets[0], self.targets[-1]
        first_rows, last_rows = self._rows_for(first["race_key"]), self._rows_for(last["race_key"])
        self.assertLessEqual(max(int(row["context_prior_sample_count"]) for row in first_rows), max(int(row["context_prior_sample_count"]) for row in last_rows))

    def test_rating_counts_advance_between_fixture_dates(self) -> None:
        first, last = self.targets[0], self.targets[-1]
        self.assertLessEqual(max(int(row["rating_prior_nankan_races"]) for row in self._rows_for(first["race_key"])), max(int(row["rating_prior_nankan_races"]) for row in self._rows_for(last["race_key"])))

    def test_exchange_still_not_updates(self) -> None:
        dates = rating.load_nankan_races(rating.load_class_rows())
        exchange = next(race for races in dates.values() for race in races if rating.is_exchange_excluded(race["class_row"])[0])
        result = rating.run_rating({exchange["race_date"]: [exchange]}, "R3", 1.0, include_outputs=True)
        self.assertEqual(sum(value for key, value in result["update_stats"].items() if key.startswith("rating_update")), 0)

    def test_other_flat_still_not_updates(self) -> None:
        source = (rating.ROOT / "src/audit/p2_m03a_empirical_rating_protocol.py").read_text(encoding="utf-8")
        self.assertIn("venue_class = 'NANKAN_TARGET'", source)
        self.assertNotIn("OTHER_FLAT_RESULT_UPDATES = 1", source)


if __name__ == "__main__":
    unittest.main()
