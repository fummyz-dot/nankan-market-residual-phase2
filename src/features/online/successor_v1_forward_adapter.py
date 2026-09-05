"""Provider-injected exact feature boundary for the frozen Fold4 scorer."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
PRIMARY_COUNT = 129
PRIMARY_HASH = "f2d11d6632c94c3826343f5ce3051ebb9d21d26b2c5754ea38a6f06c20604aa5"
RACE_HEAD_COUNT = 32
RACE_HEAD_HASH = "d65c205307ea63b58b3f284530d6daa747f04bb3411c068c3430735860a11303"
MODES = {"T15_PREDICTION", "POST_SETTLEMENT_EB_UPDATE"}
OUTCOME_FIELDS = {
    "finish_position", "result_status", "payout", "payouts", "settlement",
    "target_z", "actual_top3", "winning_pairs", "wide_hit",
}


class ForwardAdapterError(RuntimeError):
    pass


def encode_jockey_affiliation(source_status: str, raw_value: str | None) -> str:
    if source_status == "EXPLICIT_EMPTY" and (raw_value is None or not raw_value.strip()):
        return "__MISSING__"
    if source_status == "EXPLICIT_VALUE" and raw_value is not None and raw_value.strip():
        return raw_value.strip()
    raise ForwardAdapterError("JOCKEY_AFFILIATION_SOURCE_UNRESOLVED")


def encode_prize_features(prizes: Mapping[int, Mapping[str, Any]]) -> dict[str, float | None]:
    if set(prizes) != set(range(1, 6)):
        raise ForwardAdapterError("PRIZE_SOURCE_ORDINALS_UNRESOLVED")
    values: list[int | None] = []
    for place in range(1, 6):
        item = prizes[place]
        status, value = item.get("source_status"), item.get("yen")
        if status == "EXPLICIT_NOT_PUBLISHED" and value is None:
            values.append(None)
        elif status == "EXPLICIT_VALUE_YEN" and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values.append(value)
        else:
            raise ForwardAdapterError(f"PRIZE_SOURCE_UNRESOLVED:{place}")
    present = [value for value in values if value is not None]
    return {
        "log_prize_1": math.log1p(values[0]) if values[0] is not None else None,
        "log_prize_total": math.log1p(sum(present)) if present else None,
    }


class HistorySource(Protocol):
    def materialize(self, target_rows: pd.DataFrame, target_date: str) -> pd.DataFrame: ...
    @property
    def max_history_date(self) -> str | None: ...


class TargetRaceSource(Protocol):
    def target_rows(self, race_key: str, target_date: str, mode: str) -> pd.DataFrame: ...


def _ordered_manifest(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: int(row["ordered_position"]))
    return [row["feature_name"] for row in rows if row.get("included", "True").lower() != "false"]


def ordered_hash(names: Iterable[str], *, newline_joined: bool = False) -> str:
    values = list(names)
    payload = ("\n".join(values).encode() if newline_joined else
               json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode())
    return hashlib.sha256(payload).hexdigest()


PRIMARY_NAMES = _ordered_manifest(ROOT / "data/manifests/successor_v1/PRIMARY_MODEL_INPUT_MANIFEST_V1.csv")
RACE_HEAD_NAMES = _ordered_manifest(ROOT / "data/manifests/successor_v1/RACE_HEAD_INPUT_MANIFEST_V1.csv")
if len(PRIMARY_NAMES) != PRIMARY_COUNT or ordered_hash(PRIMARY_NAMES) != PRIMARY_HASH:
    raise ForwardAdapterError("PRIMARY129_MANIFEST_MISMATCH")
if len(RACE_HEAD_NAMES) != RACE_HEAD_COUNT or ordered_hash(RACE_HEAD_NAMES, newline_joined=True) != RACE_HEAD_HASH:
    raise ForwardAdapterError("RACEHEAD32_MANIFEST_MISMATCH")


def categorical_names(model: str) -> list[str]:
    path = ROOT / "data/manifests/successor_v1/CATBOOST_INPUT_ROLE_MANIFEST_V1.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [row["feature_name"] for row in rows if row["model"] == model and row["feature_role"] == "CATEGORICAL"]


PRIMARY_CATEGORICAL = categorical_names("PRIMARY")
RACE_HEAD_CATEGORICAL = ["venue", "race_type", "surface", "direction", "class_code", "age_condition_code"]


def reject_outcome_fields(columns: Iterable[str]) -> None:
    bad = [name for name in columns if name.lower() in OUTCOME_FIELDS]
    if bad:
        raise ForwardAdapterError(f"OUTCOME_FIELD_FORBIDDEN:{','.join(sorted(bad))}")


def validate_history_boundary(max_history_date: str | None, target_date: str) -> None:
    if max_history_date is not None and date.fromisoformat(max_history_date) >= date.fromisoformat(target_date):
        raise ForwardAdapterError("SAME_DAY_OR_FUTURE_HISTORY")


def validate_exact_frame(frame: pd.DataFrame, names: Sequence[str], expected_hash: str, *, newline_joined: bool = False) -> None:
    if list(frame.columns) != list(names):
        raise ForwardAdapterError("FEATURE_ORDER_MISMATCH")
    if ordered_hash(names, newline_joined=newline_joined) != expected_hash:
        raise ForwardAdapterError("FEATURE_HASH_MISMATCH")
    if len(names) == 178:
        raise ForwardAdapterError("LEGACY178_FORBIDDEN")


@dataclass(frozen=True)
class AdaptedRace:
    race_key: str
    race_date: str
    horse_numbers: tuple[int, ...]
    primary: pd.DataFrame
    race_head: pd.DataFrame
    max_history_date: str | None


def adapt_materialized_rows(rows: pd.DataFrame) -> AdaptedRace:
    if rows.empty or rows["race_key"].nunique() != 1 or rows["race_date"].nunique() != 1:
        raise ForwardAdapterError("TARGET_RACE_ROWS_INVALID")
    rows = rows.sort_values("horse_number", kind="stable").reset_index(drop=True)
    target_date = str(rows.loc[0, "race_date"])[:10]
    max_dates = [str(value)[:10] for value in rows.get("max_source_result_date", pd.Series(dtype=object)).dropna() if str(value).strip()]
    max_history = max(max_dates) if max_dates else None
    validate_history_boundary(max_history, target_date)
    missing = [name for name in PRIMARY_NAMES if name not in rows]
    if missing:
        raise ForwardAdapterError(f"PRIMARY_SOURCE_FIELDS_MISSING:{','.join(missing)}")
    primary = rows.loc[:, PRIMARY_NAMES].copy()
    validate_exact_frame(primary, PRIMARY_NAMES, PRIMARY_HASH)
    head_values: dict[str, Any] = {}
    for name in RACE_HEAD_NAMES:
        values = primary[name]
        if name in RACE_HEAD_CATEGORICAL:
            normalized = values.fillna("__MISSING__").astype(str)
            if normalized.nunique(dropna=False) != 1:
                raise ForwardAdapterError(f"RACE_HEAD_NOT_CONSTANT:{name}")
            head_values[name] = normalized.iloc[0]
        else:
            numeric = pd.to_numeric(values, errors="coerce")
            finite = numeric.dropna()
            if len(finite) and float(finite.max() - finite.min()) > 1e-12:
                raise ForwardAdapterError(f"RACE_HEAD_NOT_CONSTANT:{name}")
            if len(finite) != len(numeric) and len(finite):
                raise ForwardAdapterError(f"RACE_HEAD_MIXED_MISSING:{name}")
            head_values[name] = float(finite.iloc[0]) if len(finite) else float("nan")
    race_head = pd.DataFrame([head_values], columns=RACE_HEAD_NAMES)
    validate_exact_frame(race_head, RACE_HEAD_NAMES, RACE_HEAD_HASH, newline_joined=True)
    return AdaptedRace(
        str(rows.loc[0, "race_key"]), target_date,
        tuple(rows["horse_number"].astype(int)), primary, race_head, max_history,
    )


def materialize_pre_race_safe_inputs(
    history_source: HistorySource,
    target_source: TargetRaceSource,
    *, race_key: str,
    target_date: str,
    mode: str,
    phase: str,
) -> AdaptedRace:
    if mode not in MODES or phase != "PHASE_B":
        raise ForwardAdapterError("PHASE_B_TARGET_MODE_REQUIRED")
    target = target_source.target_rows(race_key, target_date, mode)
    reject_outcome_fields(target.columns)
    validate_history_boundary(history_source.max_history_date, target_date)
    return adapt_materialized_rows(history_source.materialize(target, target_date))


def open_phase_b_live_history_source(*, phase: str, target_date: str, **kwargs: Any) -> Any:
    if phase != "PHASE_B":
        raise ForwardAdapterError("LIVE_HISTORY_PROVIDER_LOCKED_UNTIL_PHASE_B")
    from src.features.online.normalized_history_provider import P2NormalizedHistoricalAsOfProvider
    return P2NormalizedHistoricalAsOfProvider(target_date=target_date, **kwargs)


class Primary129ForwardState:
    """Exact Job003/003B state continued one settled date at a time.

    Target materialization is read-only.  ``update_settled_date`` is the only
    mutation point and callers must invoke it only after every prediction on
    that date has been frozen.
    """

    SUPPORT_FIELDS = (
        "prior_starts", "starts_last_30d", "starts_last_90d", "starts_last_365d",
        "same_venue_starts", "same_distance_starts", "same_venue_distance_starts",
        "same_surface_starts", "same_direction_starts", "jockey_90d_starts",
        "jockey_365d_starts", "trainer_90d_starts", "trainer_365d_starts",
        "near_distance_200m_starts", "same_venue_near_distance_200m_starts",
        "same_direction_distance_starts",
    )

    def __init__(self) -> None:
        from src.audit.p2s_job003_materialized_feature_foundation import StandardState

        self.horse: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.jockey: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.trainer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.speed: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.pace: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.jockey_participation: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.trainer_participation: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.standard = StandardState()
        self.max_history_date: str | None = None

    @staticmethod
    def _eligible(race: Mapping[str, Any]) -> bool:
        starters = [row for row in race["runners"] if row.get("result_status") in {"FINISHED", "DNF"}]
        top = {
            rank: [row for row in starters if row.get("result_status") == "FINISHED" and row.get("finish_position") == rank]
            for rank in (1, 2, 3)
        }
        return len(starters) >= 3 and all(len(top[rank]) == 1 for rank in top) and len(
            {top[rank][0]["horse_number"] for rank in top}
        ) == 3

    @classmethod
    def from_historical_races(cls, races: Mapping[str, dict[str, Any]]) -> "Primary129ForwardState":
        """Rebuild the frozen state through the supplied historical cutoff."""
        from src.audit.p2s_job003_materialized_feature_foundation import class_values, mean

        state = cls()
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source in races.values():
            race = dict(source)
            race["runners"] = [dict(row) for row in source["runners"]]
            race["_class"] = class_values(race)
            by_date[str(race["race_date"])].append(race)
        for race_date in sorted(by_date):
            field_strengths: dict[str, float | None] = {}
            for race in by_date[race_date]:
                if state._eligible(race):
                    values = [
                        mean([event["z"] for event in state.horse[row["horse_key"]] if event.get("z") is not None])
                        for row in race["runners"]
                    ]
                    field_strengths[race["race_key"]] = mean([value for value in values if value is not None])
            state.update_settled_date(by_date[race_date], field_strengths=field_strengths)
        return state

    @staticmethod
    def _count_support(records: list[dict[str, Any]], target: date, days: int | None = None) -> int:
        return sum(days is None or (target - row["day"]).days <= days for row in records)

    def _support_values(self, race: Mapping[str, Any], runner: Mapping[str, Any], target: date) -> dict[str, int]:
        from src.audit.p2s_job003_materialized_feature_foundation import clean

        horse = self.horse[str(runner["horse_key"])]
        jockey = self.jockey_participation[clean(runner["jockey"])]
        trainer = self.trainer_participation[clean(runner["trainer"])]
        return {
            "prior_starts": len(horse),
            "starts_last_30d": self._count_support(horse, target, 30),
            "starts_last_90d": self._count_support(horse, target, 90),
            "starts_last_365d": self._count_support(horse, target, 365),
            "same_venue_starts": sum(row["venue"] == race["venue"] for row in horse),
            "same_distance_starts": sum(row["distance_m"] == race["distance_m"] for row in horse),
            "same_venue_distance_starts": sum(row["venue"] == race["venue"] and row["distance_m"] == race["distance_m"] for row in horse),
            "same_surface_starts": sum(row["surface"] == race["surface"] for row in horse),
            "same_direction_starts": sum(row["direction"] == race["direction"] for row in horse),
            "jockey_90d_starts": self._count_support(jockey, target, 90),
            "jockey_365d_starts": self._count_support(jockey, target, 365),
            "trainer_90d_starts": self._count_support(trainer, target, 90),
            "trainer_365d_starts": self._count_support(trainer, target, 365),
            "near_distance_200m_starts": sum(abs(row["distance_m"] - race["distance_m"]) <= 200 for row in horse),
            "same_venue_near_distance_200m_starts": sum(row["venue"] == race["venue"] and abs(row["distance_m"] - race["distance_m"]) <= 200 for row in horse),
            "same_direction_distance_starts": sum(row["direction"] == race["direction"] and abs(row["distance_m"] - race["distance_m"]) <= 200 for row in horse),
        }

    def materialize_race(self, race: dict[str, Any]) -> AdaptedRace:
        """Create the exact ordered 129/32 frames without changing history."""
        from src.audit.p2s_job003_materialized_feature_foundation import (
            b0_row, class_values, clean, composition, entity_features, primary_pre,
        )

        reject_outcome_fields(race.keys())
        target = date.fromisoformat(str(race["race_date"])[:10])
        validate_history_boundary(self.max_history_date, target.isoformat())
        source = dict(race)
        source["_class"] = class_values(source)
        rows: list[dict[str, Any]] = []
        for runner in source["runners"]:
            reject_outcome_fields(runner.keys())
            jockey_key, trainer_key = clean(runner["jockey"]), clean(runner["trainer"])
            base = b0_row(
                source, runner, target, self.horse[str(runner["horse_key"])],
                self.jockey[jockey_key], self.trainer[trainer_key],
                entity_features(self.jockey[jockey_key], target, "jockey"),
                entity_features(self.trainer[trainer_key], target, "trainer"),
                PRIMARY_HASH, "FORWARD_COMBINED_HISTORY",
            )
            base.update(self._support_values(source, runner, target))
            row = {
                **base,
                **primary_pre(
                    source, runner, base, self.horse[str(runner["horse_key"])],
                    self.speed[str(runner["horse_key"])], self.pace[str(runner["horse_key"])],
                ),
            }
            row["historical_roster_proxy"] = False
            row["feature_manifest_hash"] = PRIMARY_HASH
            rows.append(row)
        composition(rows)
        frame = pd.DataFrame(rows).sort_values("horse_number", kind="stable").reset_index(drop=True)
        return adapt_materialized_rows(frame)

    def update_settled_date(
        self, races: Sequence[dict[str, Any]], *, field_strengths: Mapping[str, float | None]
    ) -> None:
        """Append one complete date only; mixed/same-day partial updates fail."""
        from src.audit.p2s_job003_materialized_feature_foundation import (
            clean, course, exchange, source_events,
        )

        if not races:
            return
        dates = {str(race["race_date"])[:10] for race in races}
        if len(dates) != 1:
            raise ForwardAdapterError("EB_UPDATE_MIXED_DATES")
        race_date = dates.pop()
        if self.max_history_date is not None and race_date <= self.max_history_date:
            raise ForwardAdapterError("EB_UPDATE_NON_MONOTONIC_DATE")
        target = date.fromisoformat(race_date)
        standards = {race["race_key"]: self.standard.standard(race) for race in races}
        clocks: list[tuple[dict[str, Any], float]] = []
        all_events: list[dict[str, Any]] = []
        for race in races:
            events = source_events(race, target, standards[race["race_key"]])
            for event in events:
                event["field_strength"] = field_strengths.get(race["race_key"])
            all_events.extend(events)
            valid = [
                float(row["finish_time_seconds"]) for row in race["runners"]
                if row.get("result_status") == "FINISHED" and row.get("finish_time_seconds") is not None
            ]
            starters = sum(row.get("result_status") in {"FINISHED", "DNF"} for row in race["runners"])
            if not exchange(race) and len(valid) >= 3 and starters and len(valid) / starters >= 0.5:
                clocks.append((race, statistics.median(valid)))
        for event in all_events:
            self.horse[str(event["horse"])].append(event)
            participation = {key: event[key] for key in ("day", "venue", "distance_m", "surface", "direction")}
            self.jockey_participation[str(event["jockey"])].append(participation)
            self.trainer_participation[str(event["trainer"])].append(participation)
            if event.get("finish_pct") is not None:
                self.jockey[str(event["jockey"])].append(event)
                self.trainer[str(event["trainer"])].append(event)
            if event.get("speed") is not None:
                self.speed[str(event["horse"])].append({"day": target, "z": event["speed"], "course": event["course"]})
            if event.get("rank") is not None or event.get("front") is not None:
                self.pace[str(event["horse"])].append({"day": target, "rank": event.get("rank"), "adv": event.get("adv"), "front": event.get("front")})
        for race, clock in clocks:
            self.standard.update(race, clock)
        self.standard.last_date = race_date
        self.max_history_date = race_date
