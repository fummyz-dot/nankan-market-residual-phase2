"""Online target adapter for the frozen M03 class builders.

The implementation reuses M03A's date-block rating engine and M03B's feature
row builder.  A target row has no result and can therefore never update state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.audit import p2_m03a_empirical_rating_protocol as rating
from src.audit import p2_m03b_empirical_class_feature_build as class_builder


CLASS_FIELDS = (
    "ruleset_id", "class_top_code", "class_bottom_code", "class_top_ordinal", "class_bottom_ordinal", "mixed_class_flag", "race_taxonomy_code", "race_grade_code",
    "rating_pre", "field_strength_shrunk_mean", "runner_strength_delta", "race_strength_delta", "official_class_top_step", "official_class_bottom_step", "official_class_direction",
    "rating_prior_nankan_races", "rating_prior_valid_pairs", "days_since_last_nankan_rating_race", "cold_start_flag", "rating_information_depth", "field_rating_coverage", "context_prior_sample_count", "context_fallback_level", "initial_global_zero_flag",
)


def _target_race(target: dict[str, Any]) -> dict[str, Any]:
    required = {"race_key", "race_date", "venue", "race_number", "field_size", "class_row", "runners"}
    missing = sorted(key for key in required if key not in target)
    if missing:
        raise ValueError(f"online class target missing fields: {missing}")
    runners = []
    for runner in target["runners"]:
        if not {"horse_identity_key", "horse_number"} <= set(runner):
            raise ValueError("online class runner identity missing")
        runners.append({"horse_identity_key": runner["horse_identity_key"], "horse_number": runner["horse_number"], "finish_position": None, "result_status": "TARGET_PENDING"})
    if len({int(row["horse_number"]) for row in runners}) != len(runners) or len(runners) != int(target["field_size"]):
        raise ValueError("online class target roster invalid")
    return {key: target[key] for key in ("race_key", "race_date", "venue", "race_number", "field_size")} | {"class_row": deepcopy(target["class_row"]), "runners": runners}


def _replay_class_features(
    targets: list[dict[str, Any]],
    *,
    dates: dict[str, list[dict[str, Any]]] | None = None,
    class_rows: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Replay actual historical updates while emitting target pre-date state.

    A historical fixture is selected from the real historical race stream, so
    its result is applied after that date's output and remains available to
    later targets. A future/live target is added as a non-updating virtual race.
    """
    virtual = [_target_race(target) for target in targets]
    target_keys = {row["race_key"] for row in virtual}
    if len(target_keys) != len(virtual):
        raise ValueError("duplicate online class target race")
    selected = class_builder.parse_selected()
    class_rows = deepcopy(class_rows if class_rows is not None else rating.load_class_rows())
    dates = deepcopy(dates if dates is not None else rating.load_nankan_races(class_rows))
    historical_keys = {race["race_key"] for races in dates.values() for race in races}
    for race in virtual:
        if race["race_key"] in historical_keys:
            # Historical fake-live parity selects the existing event. Its
            # actual result must remain in the replay after its date closes.
            continue
        class_rows[race["race_key"]] = race["class_row"]
        dates.setdefault(race["race_date"], []).append(race)
    dates = {race_date: sorted(races, key=lambda row: row["race_key"]) for race_date, races in sorted(dates.items())}
    rebuilt = rating.run_rating(dates, selected["selected_config"], float(selected["selected_k"]), include_outputs=True)
    runner, race, _ = class_builder.build_feature_rows(dates, class_rows, rebuilt["outputs"])
    race_by_key = {row["race_key"]: row for row in race if row["race_key"] in target_keys}
    output = []
    for row in runner:
        if row["race_key"] not in target_keys:
            continue
        source = race_by_key[row["race_key"]]
        output.append({
            "race_key": row["race_key"], "race_date": row["race_date"], "venue": row["venue"],
            "horse_identity_key": row["horse_identity_key"], "horse_number": row["horse_number"],
            **{name: row[name] for name in CLASS_FIELDS if name in row},
            "field_strength_shrunk_mean": source["field_strength_shrunk_mean"],
            "field_rating_coverage": source["field_rating_coverage"],
            "context_prior_sample_count": source["context_prior_sample_count"],
            "context_fallback_level": source["context_fallback_level"],
            "initial_global_zero_flag": source["initial_global_zero_flag"],
        })
    output.sort(key=lambda row: (row["race_date"], row["race_key"], int(row["horse_number"])))
    expected = sum(len(row["runners"]) for row in virtual)
    if len(output) != expected:
        raise RuntimeError("online class output target roster mismatch")
    return output


def build_online_class_features(targets: list[dict[str, Any]], history_provider: Any | None = None) -> list[dict[str, Any]]:
    """Materialize 24 class fields using the canonical chronological replay."""
    if history_provider is None:
        return _replay_class_features(targets)
    dates, class_rows = history_provider.class_history_asof()
    return _replay_class_features(targets, dates=dates, class_rows=class_rows)


def build_online_class_features_with_fixture_state(
    targets: list[dict[str, Any]], *, dates: dict[str, list[dict[str, Any]]], class_rows: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Test-only state injection for date-boundary leakage regression tests."""
    return _replay_class_features(targets, dates=dates, class_rows=class_rows)


def historical_fixture_class_targets(race_keys: set[str]) -> list[dict[str, Any]]:
    """Create parity-only result-free targets from frozen historical inputs."""
    class_rows = rating.load_class_rows()
    dates = rating.load_nankan_races(class_rows)
    targets = []
    for races in dates.values():
        for race in races:
            if race["race_key"] in race_keys:
                targets.append({**{key: race[key] for key in ("race_key", "race_date", "venue", "race_number", "field_size")}, "class_row": class_rows[race["race_key"]], "runners": [{"horse_identity_key": runner["horse_identity_key"], "horse_number": runner["horse_number"]} for runner in race["runners"]]})
    if {row["race_key"] for row in targets} != race_keys:
        raise ValueError("historical class fixture race missing")
    return targets
