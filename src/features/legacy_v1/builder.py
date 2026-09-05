"""Strict calendar-date V1 legacy feature builder for Phase 2 active data."""

from __future__ import annotations

import math
import sqlite3
import csv
import gzip
from collections import Counter, defaultdict
from datetime import date
from statistics import mean
from typing import Any

from .contracts import CATEGORICAL_FEATURES, LEGACY_FEATURES, MISSING_CATEGORY, NONSTARTER_STATUSES, STARTER_STATUSES
from .relative import apply_relative_features
from .rolling import RollingIndex

STATUS_FROM_MARGIN = {
    "競走中止": "DNF",
    "出走取消": "SCRATCHED",
    "競走除外": "EXCLUDED",
    "競走取止め": "RACE_CANCELLED",
    "競走不成立": "RACE_NOT_ESTABLISHED",
}


def clean_category(value: Any) -> str:
    return MISSING_CATEGORY if value is None or str(value).strip() == "" else str(value).strip()


def birth_age(birth_date: str | None, target_date: date) -> int | None:
    if not birth_date:
        return None
    born = date.fromisoformat(birth_date)
    return target_date.year - born.year - ((target_date.month, target_date.day) < (born.month, born.day))


def reconstruct_v1_status(p2_status: str, margin_raw: str | None) -> str:
    """Use the audited raw-vocabulary mapping; unknown input is a hard error."""
    if p2_status == "FINISHED":
        return "FINISHED"
    if p2_status != "RAW_FINISH_STATUS_MISSING" or margin_raw not in STATUS_FROM_MARGIN:
        raise ValueError(f"unresolved V1 status reconstruction: {p2_status!r}, {margin_raw!r}")
    return STATUS_FROM_MARGIN[margin_raw]


def stats_values(state):
    if not state:
        return 0, 0, 0, None, None
    starts, wins, top3 = state
    return starts, wins, top3, wins / starts if starts else None, top3 / starts if starts else None


def add_stat(store, key, win: bool, top3: bool) -> None:
    if key is None:
        return
    state = store.setdefault(key, [0, 0, 0])
    state[0] += 1
    state[1] += int(win)
    state[2] += int(top3)


def aggregate_daily(records, kind: str):
    result = {}
    for record in records:
        if not record["normal_finish"]:
            continue
        if kind == "jockey":
            key = record["jockey"]
        elif kind == "trainer":
            key = record["trainer"]
        elif kind == "jockey_venue":
            key = (record["jockey"], record["venue"])
        else:
            key = (record["trainer"], record["venue"])
        if key is None or (isinstance(key, tuple) and key[0] is None):
            continue
        value = result.setdefault(key, [0, 0, 0])
        value[0] += 1
        value[1] += int(record["finish_position"] == 1)
        value[2] += int(record["finish_position"] <= 3)
    return result


def load_static_horse_semantics(path: str | None) -> dict[str, dict[str, str]]:
    """Read an active, frozen V1-semantic correction map (never V1 at runtime)."""
    if path is None:
        return {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return {row["horse_identity_key"]: row for row in csv.DictReader(handle)}


def load_records(history_db, static_horse_semantics_path: str | None = None) -> list[dict]:
    static_horse_semantics = load_static_horse_semantics(static_horse_semantics_path)
    conn = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT r.race_key, r.race_date, r.venue, r.race_number, r.surface, r.direction,
               r.distance_m, r.field_size, rr.horse_identity_key, rr.frame_number,
               rr.horse_number, rr.jockey, rr.trainer, rr.assigned_weight, rr.body_weight,
               rr.finish_position, rr.result_status, rr.finish_time_seconds, rr.margin_raw,
               h.birth_date, h.sex, h.sire, h.damsire
        FROM races r
        JOIN race_runners rr ON rr.race_key=r.race_key
        JOIN horses h ON h.horse_identity_key=rr.horse_identity_key
        WHERE r.venue_class='NANKAN_TARGET' AND r.race_date BETWEEN '2020-01-01' AND '2026-07-31'
        ORDER BY r.race_date, r.race_key, rr.horse_number
        """
    ).fetchall()
    conn.close()
    records = []
    for raw in rows:
        item = dict(raw)
        static = static_horse_semantics.get(item["horse_identity_key"])
        if static is not None:
            item["sex"] = static["sex_v1"]
        item["date"] = date.fromisoformat(item["race_date"])
        item["v1_status"] = reconstruct_v1_status(item.pop("result_status"), item.pop("margin_raw"))
        item["normal_finish"] = item["v1_status"] == "FINISHED" and isinstance(item["finish_position"], int) and item["finish_position"] > 0
        records.append(item)
    return records


def _online_target_record(target: dict[str, Any]) -> dict[str, Any]:
    """Normalize an already-validated pre-race target into V1 builder input.

    The target is deliberately non-updating: it can be scored using strictly
    earlier state but can never contribute a current/future result observation.
    """
    required = {
        "race_key", "race_date", "venue", "race_number", "surface", "direction", "distance_m", "field_size",
        "horse_identity_key", "frame_number", "horse_number", "jockey", "trainer", "assigned_weight",
        "birth_date", "sex", "sire", "damsire",
    }
    missing = sorted(key for key in required if key not in target)
    if missing:
        raise ValueError(f"online V1 target missing required pre-race fields: {missing}")
    target_date = date.fromisoformat(str(target["race_date"]))
    return {
        **target,
        "date": target_date,
        "body_weight": target.get("body_weight"),
        "finish_position": None,
        "finish_time_seconds": None,
        "margin_raw": None,
        "v1_status": "TARGET_PENDING",
        "normal_finish": False,
        "online_target": True,
    }


def build_legacy_features(
    history_db,
    order: str = "p2",
    static_horse_semantics_path: str | None = None,
    online_targets: list[dict[str, Any]] | None = None,
    history_records: list[dict[str, Any]] | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    # Provider injection substitutes only the loader.  All frozen V1 state,
    # date-block, relative-feature and output logic below remains shared.
    records = list(history_records) if history_records is not None else load_records(history_db, static_horse_semantics_path)
    target_records = [_online_target_record(target) for target in (online_targets or [])]
    target_keys = {(row["race_key"], int(row["horse_number"])) for row in target_records}
    if len(target_keys) != len(target_records):
        raise ValueError("duplicate online V1 target runner key")
    records.extend(target_records)
    by_date, by_race = defaultdict(list), defaultdict(list)
    for item in records:
        by_date[item["date"]].append(item)
        by_race[item["race_key"]].append(item)

    winner_time = {}
    v1_relative_members = set()
    for race_key, race_rows in by_race.items():
        winners = [x for x in race_rows if x["normal_finish"] and x["finish_position"] == 1 and x["finish_time_seconds"] is not None]
        winner_time[race_key] = float(winners[0]["finish_time_seconds"]) if len(winners) == 1 else None
        known = {x["v1_status"] for x in race_rows}
        valid_target_race = len([x for x in race_rows if x["normal_finish"] and x["finish_position"] == 1]) == 1 and known <= (STARTER_STATUSES | NONSTARTER_STATUSES)
        for item in race_rows:
            item["time_behind"] = float(item["finish_time_seconds"]) - winner_time[race_key] if item["normal_finish"] and item["finish_time_seconds"] is not None and winner_time[race_key] is not None else None
            denominator = max(int(item["field_size"]) - 1, 1) if item["field_size"] is not None else None
            item["finish_percentile"] = (int(item["finish_position"]) - 1) / denominator if item["normal_finish"] and denominator else None
            item["v1_target_member"] = bool(item.get("online_target")) or (valid_target_race and item["v1_status"] in STARTER_STATUSES)
            if item["v1_target_member"]:
                v1_relative_members.add((item["race_key"], int(item["horse_number"])))

    horse_starts, horse_finished = defaultdict(list), defaultdict(list)
    condition_stats = {name: {} for name in ("same_venue", "same_distance", "same_venue_distance", "same_surface")}
    horse_jockey_stats = {}
    j90, j365, jv365 = RollingIndex(90), RollingIndex(365), RollingIndex(365)
    t90, t365, tv365 = RollingIndex(90), RollingIndex(365), RollingIndex(365)
    output, same_day_candidates = [], 0

    for target_date in sorted(by_date):
        today = by_date[target_date]
        daily_sources = [x for x in today if x["v1_status"] in STARTER_STATUSES]
        same_day_candidates += len(daily_sources)
        daily_rows = defaultdict(list)
        for source in today:
            horse_key = source["horse_identity_key"]
            prior_starts, prior_finished = horse_starts[horse_key], horse_finished[horse_key]
            last_start = prior_starts[-1] if prior_starts else None
            second_start = prior_starts[-2] if len(prior_starts) >= 2 else None
            last_finish = prior_finished[-1] if prior_finished else None
            latest3, latest5 = prior_finished[-3:], prior_finished[-5:]
            date_gaps = [(target_date - x["date"]).days for x in prior_starts]
            def values(rows, column): return [float(x[column]) for x in rows if x[column] is not None]
            fp3, fp5 = values(latest3, "finish_percentile"), values(latest5, "finish_percentile")
            tb3, tb5 = values(latest3, "time_behind"), values(latest5, "time_behind")
            row = {
                "race_key": source["race_key"], "race_date": source["race_date"], "horse_identity_key": horse_key,
                "horse_number": source["horse_number"], "__v1_target_member": source["v1_target_member"], "__online_target": bool(source.get("online_target")), "venue": clean_category(source["venue"]), "race_number": source["race_number"],
                "distance_m": source["distance_m"], "surface": clean_category(source["surface"]), "direction": clean_category(source["direction"]),
                "calendar_month": target_date.month, "day_of_week": target_date.weekday(), "frame_number": source["frame_number"],
                "sex": clean_category(source["sex"]), "age": birth_age(source["birth_date"], target_date), "assigned_weight": source["assigned_weight"],
                "jockey": clean_category(source["jockey"]), "trainer": clean_category(source["trainer"]), "sire": clean_category(source["sire"]), "damsire": clean_category(source["damsire"]),
                "days_since_last_race": (target_date-last_start["date"]).days if last_start else None,
                "days_since_second_last_race": (target_date-second_start["date"]).days if second_start else None,
                "starts_last_30d": sum(gap <= 30 for gap in date_gaps), "starts_last_60d": sum(gap <= 60 for gap in date_gaps), "starts_last_90d": sum(gap <= 90 for gap in date_gaps),
                "last1_finish_percentile": last_finish["finish_percentile"] if last_finish else None,
                "mean_last3_finish_percentile": mean(fp3) if fp3 else None, "mean_last5_finish_percentile": mean(fp5) if fp5 else None,
                "best_last3_finish_percentile": min(fp3) if fp3 else None, "best_last5_finish_percentile": min(fp5) if fp5 else None,
                "prior_race_count_available": len(prior_finished), "prior3_count": len(latest3), "prior5_count": len(latest5),
                "last1_time_behind_winner": last_finish["time_behind"] if last_finish else None,
                "mean_last3_time_behind_winner": mean(tb3) if tb3 else None, "mean_last5_time_behind_winner": mean(tb5) if tb5 else None,
                "last1_body_weight": last_start["body_weight"] if last_start else None, "last2_body_weight": second_start["body_weight"] if second_start else None,
                "body_weight_delta_last1_last2": last_start["body_weight"]-second_start["body_weight"] if last_start and second_start and last_start["body_weight"] is not None and second_start["body_weight"] is not None else None,
                "last1_distance_m": last_start["distance_m"] if last_start else None,
                "abs_distance_change_from_last1": abs(source["distance_m"]-last_start["distance_m"]) if last_start and source["distance_m"] is not None and last_start["distance_m"] is not None else None,
                "same_distance_as_last1": int(source["distance_m"] == last_start["distance_m"]) if last_start and source["distance_m"] is not None and last_start["distance_m"] is not None else None,
                "same_venue_as_last1": int(source["venue"] == last_start["venue"]) if last_start and source["venue"] is not None and last_start["venue"] is not None else None,
                "same_surface_as_last1": int(source["surface"] == last_start["surface"]) if last_start and source["surface"] is not None and last_start["surface"] is not None else None,
            }
            keys = {"same_venue": (horse_key, source["venue"]), "same_distance": (horse_key, source["distance_m"]), "same_venue_distance": (horse_key, source["venue"], source["distance_m"]), "same_surface": (horse_key, source["surface"])}
            for prefix, key in keys.items():
                starts, wins, top3, win_rate, top3_rate = stats_values(condition_stats[prefix].get(key))
                row.update({f"{prefix}_starts": starts, f"{prefix}_wins": wins, f"{prefix}_top3": top3, f"{prefix}_win_rate": win_rate, f"{prefix}_top3_rate": top3_rate})
            for prefix, state in (("jockey_90d",j90.get(source["jockey"],target_date)),("jockey_365d",j365.get(source["jockey"],target_date)),("jockey_venue_365d",jv365.get((source["jockey"],source["venue"]),target_date)),("trainer_90d",t90.get(source["trainer"],target_date)),("trainer_365d",t365.get(source["trainer"],target_date)),("trainer_venue_365d",tv365.get((source["trainer"],source["venue"]),target_date))):
                starts,wins,top3=state; row.update({f"{prefix}_starts":starts,f"{prefix}_win_rate":wins/starts if starts else None,f"{prefix}_top3_rate":top3/starts if starts else None})
            starts,wins,top3,win_rate,top3_rate=stats_values(horse_jockey_stats.get((horse_key,source["jockey"]))) if source["jockey"] is not None else (0,0,0,None,None)
            row.update({"horse_jockey_prior_starts":starts,"horse_jockey_prior_wins":wins,"horse_jockey_prior_top3":top3,"horse_jockey_prior_win_rate":win_rate,"horse_jockey_prior_top3_rate":top3_rate})
            daily_rows[source["race_key"]].append((source,row))
        for race_key, group in daily_rows.items():
            rows = [pair[1] for pair in group]
            include = [pair[0]["v1_target_member"] for pair in group]
            apply_relative_features(rows, include)
            output.extend(rows)
        for source in daily_sources:
            horse_starts[source["horse_identity_key"]].append(source)
            if source["normal_finish"]:
                horse_finished[source["horse_identity_key"]].append(source)
                win,top3=source["finish_position"]==1,source["finish_position"]<=3
                add_stat(condition_stats["same_venue"],(source["horse_identity_key"],source["venue"]),win,top3)
                add_stat(condition_stats["same_distance"],(source["horse_identity_key"],source["distance_m"]),win,top3)
                add_stat(condition_stats["same_venue_distance"],(source["horse_identity_key"],source["venue"],source["distance_m"]),win,top3)
                add_stat(condition_stats["same_surface"],(source["horse_identity_key"],source["surface"]),win,top3)
                if source["jockey"] is not None: add_stat(horse_jockey_stats,(source["horse_identity_key"],source["jockey"]),win,top3)
        for index, kind in ((j90,"jockey"),(j365,"jockey"),(jv365,"jockey_venue"),(t90,"trainer"),(t365,"trainer"),(tv365,"trainer_venue")):
            index.add_daily(target_date,aggregate_daily(daily_sources,kind))
    if order == "p2":
        output.sort(key=lambda row: (row["race_date"], row["race_key"], int(row["horse_number"])))
    elif order == "v1":
        venue_order = {"FUNABASHI": 0, "KAWASAKI": 1, "OHI": 2, "URAWA": 3}
        source_venue = {"川崎": "KAWASAKI", "船橋": "FUNABASHI", "大井": "OHI", "浦和": "URAWA"}
        output.sort(key=lambda row: (row["race_date"], venue_order[source_venue[row["venue"]]], int(row["race_number"]), int(row["horse_number"])))
    else:
        raise ValueError(f"unknown output order: {order}")
    if target_records:
        output = [row for row in output if row["__online_target"] and (row["race_key"], int(row["horse_number"])) in target_keys]
    for row in output:
        row.pop("__online_target")
    if (not target_records and len(output) != 250093) or any(set(row) != {"race_key","race_date","horse_identity_key","horse_number","__v1_target_member",*LEGACY_FEATURES} for row in output):
        raise RuntimeError("unexpected V1 port output schema or row count")
    return output, {"source_rows":len(records),"v1_relative_members":len(v1_relative_members),"same_day_source_candidates_excluded":same_day_candidates,"online_target_rows":len(target_records),"online_target_updates_used":0}


def build_online_legacy_features(history_db, targets: list[dict[str, Any]], static_horse_semantics_path: str | None = None, history_records: list[dict[str, Any]] | None = None) -> tuple[list[dict], dict[str, Any]]:
    """Thin online wrapper over the frozen V1 builder; no duplicate formulas."""
    return build_legacy_features(history_db, order="p2", static_horse_semantics_path=static_horse_semantics_path, online_targets=targets, history_records=history_records)


def historical_fixture_online_targets(history_db, race_keys: set[str], static_horse_semantics_path: str | None = None) -> list[dict[str, Any]]:
    """Create result-free fake-live targets from historical pre-race columns.

    This helper is parity-fixture-only.  It intentionally excludes all target
    result fields and makes the full builder prove that it does not need them.
    """
    targets = []
    for row in load_records(history_db, static_horse_semantics_path):
        if row["race_key"] not in race_keys:
            continue
        targets.append({key: row.get(key) for key in (
            "race_key", "race_date", "venue", "race_number", "surface", "direction", "distance_m", "field_size",
            "horse_identity_key", "frame_number", "horse_number", "jockey", "trainer", "assigned_weight",
            "body_weight", "birth_date", "sex", "sire", "damsire",
        )})
    if {target["race_key"] for target in targets} != race_keys:
        raise ValueError("historical fixture race missing from V1 source")
    return targets
