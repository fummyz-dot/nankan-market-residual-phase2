"""Frozen WIN V1 feature definitions; no V1 runtime import is required."""

MISSING_CATEGORY = "__MISSING__"
STARTER_STATUSES = frozenset({"FINISHED", "DNF"})
NONSTARTER_STATUSES = frozenset({"SCRATCHED", "EXCLUDED", "RACE_CANCELLED", "RACE_NOT_ESTABLISHED"})

F0 = ("venue", "race_number", "distance_m", "surface", "direction", "calendar_month", "day_of_week")
F1 = ("frame_number", "horse_number", "sex", "age", "assigned_weight", "jockey", "trainer", "sire", "damsire")
F2 = ("days_since_last_race", "days_since_second_last_race", "starts_last_30d", "starts_last_60d", "starts_last_90d")
F3 = (
    "last1_finish_percentile", "mean_last3_finish_percentile", "mean_last5_finish_percentile",
    "best_last3_finish_percentile", "best_last5_finish_percentile", "prior_race_count_available",
    "prior3_count", "prior5_count", "last1_time_behind_winner", "mean_last3_time_behind_winner",
    "mean_last5_time_behind_winner", "last1_body_weight", "last2_body_weight",
    "body_weight_delta_last1_last2", "last1_distance_m", "abs_distance_change_from_last1",
    "same_distance_as_last1", "same_venue_as_last1", "same_surface_as_last1",
)
CONDITION_PREFIXES = ("same_venue", "same_distance", "same_venue_distance", "same_surface")
F5 = tuple(f"{prefix}_{suffix}" for prefix in CONDITION_PREFIXES for suffix in ("starts", "wins", "top3", "win_rate", "top3_rate"))
ROLLING_PREFIXES = ("jockey_90d", "jockey_365d", "jockey_venue_365d", "trainer_90d", "trainer_365d", "trainer_venue_365d")
F6 = tuple(f"{prefix}_{suffix}" for prefix in ROLLING_PREFIXES for suffix in ("starts", "win_rate", "top3_rate"))
F7 = tuple(f"horse_jockey_prior_{suffix}" for suffix in ("starts", "wins", "top3", "win_rate", "top3_rate"))
RELATIVE_BASES = (
    "assigned_weight", "age", "days_since_last_race", "last1_finish_percentile", "mean_last3_finish_percentile",
    "mean_last5_finish_percentile", "same_venue_win_rate", "same_venue_top3_rate", "same_distance_win_rate",
    "same_distance_top3_rate", "jockey_90d_win_rate", "jockey_90d_top3_rate", "jockey_365d_win_rate",
    "jockey_365d_top3_rate", "trainer_90d_win_rate", "trainer_90d_top3_rate", "trainer_365d_win_rate",
    "trainer_365d_top3_rate",
)
F8 = tuple(item for base in RELATIVE_BASES for item in (f"{base}_minus_race_mean", f"{base}_race_percentile_rank"))
LEGACY_FEATURES = F0 + F1 + F2 + F3 + F5 + F6 + F7 + F8
CATEGORICAL_FEATURES = ("venue", "surface", "direction", "sex", "jockey", "trainer", "sire", "damsire")
NUMERIC_FEATURES = tuple(x for x in LEGACY_FEATURES if x not in CATEGORICAL_FEATURES)
GROUP_BY_FEATURE = {**{x: "F0" for x in F0}, **{x: "F1" for x in F1}, **{x: "F2" for x in F2}, **{x: "F3" for x in F3}, **{x: "F5" for x in F5}, **{x: "F6" for x in F6}, **{x: "F7" for x in F7}, **{x: "F8" for x in F8}}

if len(LEGACY_FEATURES) != 119 or set(GROUP_BY_FEATURE) != set(LEGACY_FEATURES):
    raise RuntimeError("P2 V1 legacy contract must contain exactly 119 grouped features")
