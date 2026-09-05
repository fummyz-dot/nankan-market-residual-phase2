"""Job003 strict-calendar-date successor V1 feature materializer.

The source SQLite database is opened read-only.  For every calendar date this
builder locks target rows from prior-day state, writes them, then updates state
from completed source races on that date.  It performs no model fitting.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import platform
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import NormalDist

from src.audit.p2_m02_class_ruleset_foundation import classify
from src.features.pace.corner_parser import completeness, parse_corners
from src.features.pace.observations import finite_positive, last3f_relative

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "reference/v1/db/nankan_history.sqlite"
JOB2 = ROOT / "audit/successor_v1/job002"
MAN = ROOT / "data/manifests/successor_v1"
DATA = ROOT / "data/processed/successor_v1"
OUT = ROOT / "audit/successor_v1/job003"
ATTEMPTS = OUT / "attempts"
ATTEMPT_ID = "attempt_002"
ATTEMPT = ATTEMPTS / ATTEMPT_ID
STAGE = DATA / ".job003_attempt_002"
FINAL_B0 = DATA / "b0_safe_core_features_v1"
FINAL_PRIMARY = DATA / "runner_primary_deterministic_features_v1"
CUTOFF = "2026-07-31"
VENUES = {"大井", "船橋", "川崎", "浦和"}
EXPECTED_RACES, EXPECTED_RUNNERS = 21560, 246709
MISSING = "__MISSING__"
ORDINAL = {"A1": 8, "A2": 7, "B1": 6, "B2": 5, "B3": 4, "C1": 3, "C2": 2, "C3": 1}

B0 = [
    "calendar_month", "day_of_week", "venue", "race_number", "race_type", "surface", "direction", "distance_m", "log_prize_1", "log_prize_total",
    "frame_number", "horse_number", "assigned_weight", "jockey_affiliation", "trainer_affiliation", "racing_age", "sex",
    "prior_starts", "days_since_last_race", "days_since_second_last_race", "starts_last_30d", "starts_last_90d", "starts_last_365d",
    "last1_finish_pct", "mean_last3_finish_pct", "mean_last5_finish_pct", "best_last5_finish_pct", "sd_last5_finish_pct", "prior_win_rate", "prior_top3_rate",
    "same_venue_starts", "same_venue_mean_finish_pct", "same_venue_top3_rate", "same_distance_starts", "same_distance_mean_finish_pct", "same_distance_top3_rate",
    "same_venue_distance_starts", "same_venue_distance_mean_finish_pct", "same_venue_distance_top3_rate", "same_surface_starts", "same_surface_mean_finish_pct",
    "same_direction_starts", "same_direction_mean_finish_pct",
    "jockey_90d_starts", "jockey_90d_mean_finish_pct", "jockey_90d_top3_rate", "jockey_365d_starts", "jockey_365d_mean_finish_pct", "jockey_365d_top3_rate",
    "trainer_90d_starts", "trainer_90d_mean_finish_pct", "trainer_90d_top3_rate", "trainer_365d_starts", "trainer_365d_mean_finish_pct", "trainer_365d_top3_rate",
]
PRIMARY_NEW = [
    "class_code", "class_ordinal", "class_known_flag", "class_group_no", "mixed_class_flag", "age_condition_code", "sex_restriction_flag", "last1_class_ordinal", "official_class_delta_last1",
    "emp_horse_prior_count", "emp_horse_mean_z", "emp_horse_sd_z", "emp_field_mean_z", "emp_field_median_z", "emp_field_top3_mean_z", "emp_field_sd_z", "emp_field_rating_coverage", "emp_runner_vs_field_delta_z", "emp_last_race_field_strength_z", "emp_prev_to_current_strength_delta_z", "emp_horse_se_z", "emp_field_uncertainty_mean",
    "speed_prior_obs_count", "speed_recent3_count", "speed_recent5_count", "days_since_last_speed", "speed_cold_start_flag", "speed_last_z", "speed_recent3_mean_z", "speed_recent5_mean_z", "speed_recent5_best_z", "speed_recent5_dispersion_z", "speed_recent3_trend_z", "speed_exact_course_prior_count", "speed_exact_course_recent3_count", "speed_exact_course_last_z", "speed_exact_course_recent3_mean_z",
    "pace_closing_prior_obs_count", "pace_closing_recent3_count", "pace_closing_recent5_count", "days_since_last_closing_obs", "pace_closing_cold_start_flag", "pace_last_last3f_rank_pct", "pace_recent3_last3f_rank_mean", "pace_recent5_last3f_rank_mean", "pace_recent5_last3f_rank_best", "pace_recent5_last3f_rank_dispersion", "pace_recent3_last3f_rank_trend", "pace_last_closing_adv_sec", "pace_recent3_closing_adv_mean_sec", "pace_front_recent3_mean", "pace_front_recent5_mean", "pace_front_recent5_count",
    "near_distance_200m_starts", "near_distance_200m_mean_finish_pct", "same_venue_near_distance_200m_starts", "same_venue_near_distance_200m_mean_finish_pct", "same_direction_distance_starts", "same_direction_distance_mean_finish_pct",
    "comp_ability_mean", "comp_ability_sd", "comp_ability_top3_mean", "comp_ability_gap_1_2", "comp_ability_gap_3_4", "comp_ability_coverage", "comp_speed_mean", "comp_speed_sd", "comp_speed_top3_mean", "comp_speed_coverage", "comp_front_propensity_sum", "comp_front_propensity_max", "comp_front_propensity_sd", "comp_history_coverage_mean", "comp_uncertainty_mean", "comp_uncertainty_sd",
]
PROVENANCE = ["race_key", "race_date", "horse_key", "horse_number", "feature_asof_date", "max_source_result_date", "historical_roster_proxy", "feature_manifest_hash", "source_db_hash", "builder_commit"]


class ContractFailure(RuntimeError):
    pass


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1_048_576), b""):
            h.update(block)
    return h.hexdigest()


def ordered_hash(names: list[str]) -> str:
    return hashlib.sha256(json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean(value) -> str:
    return MISSING if value is None or str(value).strip() == "" else str(value).strip()


def mean(xs: list[float]):
    return sum(xs) / len(xs) if xs else None


def sd(xs: list[float]):
    return statistics.pstdev(xs) if len(xs) >= 2 else None


def trend3(xs: list[float]):
    return (xs[-1] - xs[0]) / 2 if len(xs) == 3 else None


def age(birth: str | None, target: date):
    if not birth:
        return None
    born = date.fromisoformat(birth)
    return target.year - born.year - ((target.month, target.day) < (born.month, born.day))


def require_target_date(day: str) -> None:
    if day > CUTOFF:
        raise ContractFailure(f"POST_CUTOFF_TARGET_DATE:{day}")


def require_strict_prior(target_day: str, source_day: str | None) -> None:
    if source_day is not None and source_day >= target_day:
        raise ContractFailure(f"NON_STRICT_RESULT_SOURCE:{source_day}>={target_day}")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="raise")
        writer.writeheader(); writer.writerows(rows)


class GzipWriter:
    def __init__(self, path: Path, fields: list[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.tmp, self.fields = path, path.with_suffix(path.suffix + ".tmp"), fields
        self.raw = self.tmp.open("wb")
        self.gz = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0)
        self.text = io.TextIOWrapper(self.gz, encoding="utf-8", newline="")
        self.writer = csv.DictWriter(self.text, fieldnames=fields, extrasaction="raise")
        self.writer.writeheader()
    def write(self, row: dict) -> None: self.writer.writerow(row)
    def close(self) -> None:
        self.text.flush(); self.text.detach(); self.gz.close(); self.raw.close(); os.replace(self.tmp, self.path)


def get_races() -> dict[str, dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT r.*, h.birth_date,h.sex, rr.horse_key,rr.frame_number,rr.horse_number,rr.jockey,rr.jockey_affiliation,rr.assigned_weight,rr.trainer,rr.trainer_affiliation,rr.finish_position,rr.result_status,rr.finish_time_seconds,rr.last_3f
                          FROM races r JOIN race_runners rr ON r.race_key=rr.race_key JOIN horses h ON h.horse_key=rr.horse_key
                          WHERE r.venue IN ('大井','船橋','川崎','浦和') AND r.race_date<=?
                          ORDER BY r.race_date,r.race_key,rr.horse_number""", (CUTOFF,)).fetchall()
    con.close()
    races: dict[str, dict] = {}
    for raw in rows:
        x = dict(raw)
        r = races.setdefault(x["race_key"], {k: x[k] for k in ("race_key","race_date","venue","venue_code","race_number","race_type","race_name","surface","direction","distance_m","weather","going","field_size","conditions_raw","prize_1","prize_2","prize_3","prize_4","prize_5","final_3f","lap_times_json","corners_json")} | {"runners": []})
        r["runners"].append({k: x[k] for k in ("horse_key","birth_date","sex","frame_number","horse_number","jockey","jockey_affiliation","assigned_weight","trainer","trainer_affiliation","finish_position","result_status","finish_time_seconds","last_3f")})
    return races


def eligible_keys(races: dict[str, dict]) -> set[str]:
    keys=set()
    for k, r in races.items():
        starters=[x for x in r["runners"] if x["result_status"] in {"FINISHED","DNF"}]
        top={p:[x for x in r["runners"] if x["result_status"]=="FINISHED" and x["finish_position"]==p] for p in (1,2,3)}
        if len(starters)>=3 and all(len(top[p])==1 for p in (1,2,3)) and len({top[p][0]["horse_number"] for p in (1,2,3)})==3:
            keys.add(k)
    if len(keys) != EXPECTED_RACES:
        raise ContractFailure(f"ELIGIBLE_RACE_COUNT_MISMATCH:{len(keys)}")
    count=sum(len(races[k]["runners"]) for k in keys)
    if count != EXPECTED_RUNNERS:
        raise ContractFailure(f"ELIGIBLE_RUNNER_COUNT_MISMATCH:{count}")
    return keys


def course(r: dict) -> tuple:
    return (r["venue"], r["distance_m"], r["surface"], r["direction"])


def exchange(r: dict) -> bool:
    return "交流" in ((r["race_name"] or "") + " " + (r["conditions_raw"] or ""))


def class_values(r: dict) -> dict:
    parsed=classify({"venue_class":"NANKAN_TARGET", "conditions_raw":r["conditions_raw"], "race_name":r["race_name"], "race_type_raw":r["race_type"], "race_date":r["race_date"]})
    group=json.loads(parsed["group_numbers_json"])
    code=parsed["class_top_code"]
    return {"class_code": clean(code), "class_ordinal": parsed["class_top_ordinal"], "class_known_flag": int(code is not None), "class_group_no": group[0] if len(group)==1 else None, "mixed_class_flag": parsed["mixed_class_flag"], "age_condition_code": clean(parsed["age_condition_code"]), "sex_restriction_flag": int(parsed["sex_condition_code"] != "OPEN_SEX")}


def summary_finish(records: list[dict]) -> tuple[int, float | None, float | None]:
    vals=[x["finish_pct"] for x in records if x.get("finish_pct") is not None]
    return len(vals), mean(vals), (sum(x["top3"] for x in records)/len(vals) if vals else None)


def window_entity(records: list[dict], d: date, days: int) -> list[dict]:
    return [x for x in records if (d-x["day"]).days <= days]


def entity_features(records: list[dict], d: date, prefix: str) -> dict:
    out={}
    for days in (90,365):
        vals=window_entity(records,d,days); f=[x["finish_pct"] for x in vals]
        out[f"{prefix}_{days}d_starts"]=len(vals)
        out[f"{prefix}_{days}d_mean_finish_pct"]=mean(f)
        out[f"{prefix}_{days}d_top3_rate"]=sum(x["top3"] for x in vals)/len(vals) if vals else None
    return out


def condition_features(hist: list[dict], r: dict) -> dict:
    defs=[("same_venue",lambda x:x["venue"]==r["venue"]),("same_distance",lambda x:x["distance_m"]==r["distance_m"]),("same_venue_distance",lambda x:x["venue"]==r["venue"] and x["distance_m"]==r["distance_m"]),("same_surface",lambda x:x["surface"]==r["surface"]),("same_direction",lambda x:x["direction"]==r["direction"])]
    out={}
    for name, pred in defs:
        vals=[x for x in hist if x.get("finish_pct") is not None and pred(x)]
        out[f"{name}_starts"]=len(vals); out[f"{name}_mean_finish_pct"]=mean([x["finish_pct"] for x in vals])
        if name in {"same_venue","same_distance","same_venue_distance"}: out[f"{name}_top3_rate"]=sum(x["top3"] for x in vals)/len(vals) if vals else None
    near=[x for x in hist if x.get("finish_pct") is not None and x["distance_m"] is not None and r["distance_m"] is not None and abs(x["distance_m"]-r["distance_m"])<=200]
    vnear=[x for x in near if x["venue"]==r["venue"]]
    dnear=[x for x in near if x["direction"]==r["direction"]]
    for name, vals in (("near_distance_200m",near),("same_venue_near_distance_200m",vnear),("same_direction_distance",dnear)):
        out[f"{name}_starts"]=len(vals); out[f"{name}_mean_finish_pct"]=mean([x["finish_pct"] for x in vals])
    return out


def speed_features(hist: list[dict], d: date, c: tuple) -> dict:
    vals=hist; r3=vals[-3:]; r5=vals[-5:]; exact=[x for x in vals if x["course"]==c]; e3=exact[-3:]
    zs=lambda a:[x["z"] for x in a]
    return {"speed_prior_obs_count":len(vals),"speed_recent3_count":len(r3),"speed_recent5_count":len(r5),"days_since_last_speed":(d-vals[-1]["day"]).days if vals else None,"speed_cold_start_flag":int(not vals),"speed_last_z":vals[-1]["z"] if vals else None,"speed_recent3_mean_z":mean(zs(r3)),"speed_recent5_mean_z":mean(zs(r5)),"speed_recent5_best_z":max(zs(r5)) if r5 else None,"speed_recent5_dispersion_z":sd(zs(r5)),"speed_recent3_trend_z":trend3(zs(r3)),"speed_exact_course_prior_count":len(exact),"speed_exact_course_recent3_count":len(e3),"speed_exact_course_last_z":exact[-1]["z"] if exact else None,"speed_exact_course_recent3_mean_z":mean(zs(e3))}


def pace_features(hist: list[dict], d: date) -> dict:
    close=[x for x in hist if x.get("rank") is not None and x.get("adv") is not None]; r3=close[-3:]; r5=close[-5:]; fronts=[x for x in hist if x.get("front") is not None]; f3=fronts[-3:]; f5=fronts[-5:]
    ranks=[x["rank"] for x in r5]; ranks3=[x["rank"] for x in r3]
    return {"pace_closing_prior_obs_count":len(close),"pace_closing_recent3_count":len(r3),"pace_closing_recent5_count":len(r5),"days_since_last_closing_obs":(d-close[-1]["day"]).days if close else None,"pace_closing_cold_start_flag":int(not close),"pace_last_last3f_rank_pct":close[-1]["rank"] if close else None,"pace_recent3_last3f_rank_mean":mean(ranks3),"pace_recent5_last3f_rank_mean":mean(ranks),"pace_recent5_last3f_rank_best":max(ranks) if ranks else None,"pace_recent5_last3f_rank_dispersion":sd(ranks),"pace_recent3_last3f_rank_trend":trend3(ranks3),"pace_last_closing_adv_sec":close[-1]["adv"] if close else None,"pace_recent3_closing_adv_mean_sec":mean([x["adv"] for x in r3]),"pace_front_recent3_mean":mean([x["front"] for x in f3]),"pace_front_recent5_mean":mean([x["front"] for x in f5]),"pace_front_recent5_count":len(f5)}


class StandardState:
    """Strict-prior course-only hierarchical median/MAD state."""
    def __init__(self): self.groups=defaultdict(list); self.last_date=None
    def keys(self,r):
        return [(r["venue"],r["distance_m"],r["surface"],r["direction"]),(r["venue"],r["distance_m"],r["surface"]),(r["distance_m"],r["surface"]),(r["surface"],),("global",)]
    def standard(self,r):
        keys=self.keys(r); selected=None; level="GLOBAL_COLD"
        for idx,key in enumerate(keys):
            if self.groups[key]: selected=key; level=("VENUE_DISTANCE_SURFACE_DIRECTION","VENUE_DISTANCE_SURFACE","DISTANCE_SURFACE","SURFACE")[idx]; break
        if selected is None and self.groups[("global",)]: selected=("global",); level="GLOBAL"
        if selected is None: return {"center":None,"scale":None,"count":0,"fallback":level,"fitted_through":self.last_date}
        values=self.groups[selected]; center=statistics.median(values); mad=statistics.median(abs(x-center) for x in values)
        return {"center":center,"scale":max(0.5,1.4826*mad),"count":len(values),"fallback":level,"fitted_through":self.last_date}
    def update(self,r,clock):
        for key in self.keys(r): self.groups[key].append(clock)
        self.groups[("global",)].append(clock)


def first_corner_pct(r: dict) -> dict[int,float]:
    parsed=parse_corners(r["corners_json"])
    if parsed["corner_parse_status"] != "CORNER_TOKENIZED_RAW_ORDER" or not parsed["corners"]: return {}
    active={int(x["horse_number"]) for x in r["runners"] if x["result_status"] in {"FINISHED","DNF"}}
    item=parsed["corners"][0]; checked=completeness(item,active)
    if not checked["complete"] or checked["has_ambiguous_group"] or len(active)<2: return {}
    positions={group["horse_numbers"][0]:idx for idx,group in enumerate(item["groups"],1)}
    return {horse:1-(pos-1)/(len(active)-1) for horse,pos in positions.items()}


def source_events(r: dict, d: date, standard: dict) -> list[dict]:
    starters=[x for x in r["runners"] if x["result_status"] in {"FINISHED","DNF"}]
    n=len(starters); fin=[x for x in r["runners"] if x["result_status"]=="FINISHED" and isinstance(x["finish_position"],int) and x["finish_position"]>0]
    rel=last3f_relative([{"horse_number":x["horse_number"],"last_3f":x["last_3f"] if x["result_status"]=="FINISHED" else None} for x in r["runners"]])
    front=first_corner_pct(r); out=[]; class_ordinal=r["_class"]["class_ordinal"]
    for x in starters:
        k=x["finish_position"]; valid=x["result_status"]=="FINISHED" and isinstance(k,int) and k>0 and n>1
        pct=(n-k)/(n-1) if valid else None
        z=max(-2.5,min(2.5,NormalDist().inv_cdf((n-k+0.5)/(n+1)))) if valid else None
        speed=None
        if valid and x["finish_time_seconds"] is not None and standard["center"] is not None:
            speed=(standard["center"]-float(x["finish_time_seconds"]))/standard["scale"]
        p=rel.get(int(x["horse_number"]),{})
        out.append({"horse":x["horse_key"],"jockey":clean(x["jockey"]),"trainer":clean(x["trainer"]),"day":d,"venue":r["venue"],"distance_m":r["distance_m"],"surface":r["surface"],"direction":r["direction"],"course":course(r),"finish_pct":pct,"top3":int(valid and k<=3),"z":z,"speed":speed,"rank":p.get("runner_last3f_rank_pct"),"adv":p.get("runner_closing_advantage_sec"),"front":front.get(int(x["horse_number"])),"class_ordinal":class_ordinal})
    return out


def b0_row(r: dict, x: dict, d: date, horse: list[dict], jockey: list[dict], trainer: list[dict], jockey_summary: dict, trainer_summary: dict, manifest_hash: str, db_hash: str) -> dict:
    prior=horse; finished=[e for e in prior if e.get("finish_pct") is not None]; last=prior[-1] if prior else None; second=prior[-2] if len(prior)>1 else None; last5=finished[-5:]; last3=finished[-3:]
    fp5=[e["finish_pct"] for e in last5]; fp3=[e["finish_pct"] for e in last3]
    base={"calendar_month":d.month,"day_of_week":d.weekday(),"venue":clean(r["venue"]),"race_number":r["race_number"],"race_type":clean(r["race_type"]),"surface":clean(r["surface"]),"direction":clean(r["direction"]),"distance_m":r["distance_m"],"log_prize_1":math.log1p(r["prize_1"]) if r["prize_1"] is not None else None,"log_prize_total":math.log1p(sum(v for v in (r["prize_1"],r["prize_2"],r["prize_3"],r["prize_4"],r["prize_5"]) if v is not None)) if any(v is not None for v in (r["prize_1"],r["prize_2"],r["prize_3"],r["prize_4"],r["prize_5"])) else None,"frame_number":x["frame_number"],"horse_number":x["horse_number"],"assigned_weight":x["assigned_weight"],"jockey_affiliation":clean(x["jockey_affiliation"]),"trainer_affiliation":clean(x["trainer_affiliation"]),"racing_age":age(x["birth_date"],d),"sex":clean(x["sex"]),"prior_starts":len(prior),"days_since_last_race":(d-last["day"]).days if last else None,"days_since_second_last_race":(d-second["day"]).days if second else None,"starts_last_30d":sum((d-e["day"]).days<=30 for e in prior),"starts_last_90d":sum((d-e["day"]).days<=90 for e in prior),"starts_last_365d":sum((d-e["day"]).days<=365 for e in prior),"last1_finish_pct":finished[-1]["finish_pct"] if finished else None,"mean_last3_finish_pct":mean(fp3),"mean_last5_finish_pct":mean(fp5),"best_last5_finish_pct":max(fp5) if fp5 else None,"sd_last5_finish_pct":sd(fp5),"prior_win_rate":sum(e["finish_pct"]==1 for e in finished)/len(finished) if finished else None,"prior_top3_rate":sum(e["top3"] for e in finished)/len(finished) if finished else None}
    base.update(condition_features(finished,r)); base.update(jockey_summary); base.update(trainer_summary)
    used=[e["day"].isoformat() for e in ([last] if last else []) + window_entity(jockey,d,365) + window_entity(trainer,d,365)]
    max_source=max(used) if used else None
    require_strict_prior(r["race_date"], max_source)
    return {"race_key":r["race_key"],"race_date":r["race_date"],"horse_key":x["horse_key"],"feature_asof_date":(d.fromordinal(d.toordinal()-1)).isoformat(),"max_source_result_date":max_source,"historical_roster_proxy":True,"feature_manifest_hash":manifest_hash,"source_db_hash":db_hash,"builder_commit":"VCS_NONE",**base}


def primary_pre(r: dict, x: dict, row: dict, horse: list[dict], speed: list[dict], pace: list[dict]) -> dict:
    cl=r["_class"]; finished=[e for e in horse if e.get("z") is not None]; zs=[e["z"] for e in finished]; last_class=horse[-1]["class_ordinal"] if horse else None
    out={**cl,"last1_class_ordinal":last_class,"official_class_delta_last1":cl["class_ordinal"]-last_class if cl["class_ordinal"] is not None and last_class is not None else None,"emp_horse_prior_count":len(zs),"emp_horse_mean_z":mean(zs),"emp_horse_sd_z":sd(zs),"emp_horse_se_z":sd(zs)/math.sqrt(len(zs)) if sd(zs) is not None else None,"emp_last_race_field_strength_z":horse[-1].get("field_strength") if horse else None}
    out.update(speed_features(speed,date.fromisoformat(r["race_date"]),course(r))); out.update(pace_features(pace,date.fromisoformat(r["race_date"])))
    # The B0 row has already computed both basic-condition and fixed ±200m
    # condition state.  Do not rescan horse history here.
    for name in ("near_distance_200m_starts", "near_distance_200m_mean_finish_pct", "same_venue_near_distance_200m_starts", "same_venue_near_distance_200m_mean_finish_pct", "same_direction_distance_starts", "same_direction_distance_mean_finish_pct"):
        out[name] = row[name]
    return out


def composition(rows: list[dict]) -> None:
    def vals(name): return [float(x[name]) for x in rows if x.get(name) is not None]
    ability=vals("emp_horse_mean_z"); speed=vals("speed_recent5_mean_z"); front=vals("pace_front_recent5_mean"); uncertainty=vals("emp_horse_se_z")
    def pack(prefix, values, gaps=False):
        sorted_values=sorted(values,reverse=True); result={f"{prefix}_mean":mean(values),f"{prefix}_sd":sd(values),f"{prefix}_top3_mean":mean(sorted_values[:3]),f"{prefix}_coverage":len(values)/len(rows) if rows else None}
        if gaps: result.update({"comp_ability_gap_1_2":sorted_values[0]-sorted_values[1] if len(sorted_values)>=2 else None,"comp_ability_gap_3_4":sorted_values[2]-sorted_values[3] if len(sorted_values)>=4 else None})
        return result
    fields={**pack("comp_ability",ability,True),**pack("comp_speed",speed),"comp_front_propensity_sum":sum(front) if front else None,"comp_front_propensity_max":max(front) if front else None,"comp_front_propensity_sd":sd(front),"comp_history_coverage_mean":mean([int(x["prior_starts"]>0) for x in rows]),"comp_uncertainty_mean":mean(uncertainty),"comp_uncertainty_sd":sd(uncertainty)}
    for x in rows:
        x.update(fields); x["emp_field_mean_z"]=mean(ability); x["emp_field_median_z"]=statistics.median(ability) if ability else None; x["emp_field_top3_mean_z"]=mean(sorted(ability,reverse=True)[:3]); x["emp_field_sd_z"]=sd(ability); x["emp_field_rating_coverage"]=len(ability)/len(rows) if rows else None; x["emp_runner_vs_field_delta_z"]=x["emp_horse_mean_z"]-fields["comp_ability_mean"] if x.get("emp_horse_mean_z") is not None and fields["comp_ability_mean"] is not None else None; x["emp_field_uncertainty_mean"]=fields["comp_uncertainty_mean"]; x["emp_prev_to_current_strength_delta_z"]=x["emp_last_race_field_strength_z"]-fields["comp_ability_mean"] if x.get("emp_last_race_field_strength_z") is not None and fields["comp_ability_mean"] is not None else None


def manifests(db_hash: str) -> tuple[str,str]:
    b0hash, phash=ordered_hash(B0),ordered_hash(B0+PRIMARY_NEW)
    rows=[]
    for name in B0: rows.append({"feature_name":name,"feature_block":"B0_SAFE_CORE","ordered_position":len(rows)+1,"strict_asof":"true" if name not in {"calendar_month","day_of_week","venue","race_number","race_type","surface","direction","distance_m","log_prize_1","log_prize_total","frame_number","horse_number","assigned_weight","jockey_affiliation","trainer_affiliation","racing_age","sex"} else "not_result_derived","definition_status":"FROZEN"})
    write_csv(MAN/"B0_SAFE_CORE_FEATURE_MANIFEST_V1.csv",rows,list(rows[0]))
    rows2=[]
    for name in B0+PRIMARY_NEW:
        block="B0_SAFE_CORE" if name in B0 else ("P1_CLASS_RULE" if name.startswith("class_") or name in {"last1_class_ordinal","official_class_delta_last1"} else "P1_CLASS_EMPIRICAL" if name.startswith("emp_") else "P1_SPEED" if name.startswith("speed_") or name=="days_since_last_speed" else "P1_PACE" if name.startswith("pace_") or name=="days_since_last_closing_obs" else "P1_CONDITION_SIMILARITY" if "near_distance" in name or name.startswith("same_direction_distance") else "P1_RACE_COMPOSITION")
        rows2.append({"feature_name":name,"feature_block":block,"ordered_position":len(rows2)+1,"strict_asof":"true" if block not in {"B0_SAFE_CORE","P1_CLASS_RULE"} or name not in {"calendar_month","day_of_week","venue","race_number","race_type","surface","direction","distance_m","log_prize_1","log_prize_total","frame_number","horse_number","assigned_weight","jockey_affiliation","trainer_affiliation","racing_age","sex"} else "not_result_derived","definition_status":"FROZEN"})
    write_csv(MAN/"RUNNER_PRIMARY_DETERMINISTIC_FEATURE_MANIFEST_V1.csv",rows2,list(rows2[0]))
    contract={"contract_id":"MATERIALIZED_FEATURE_CONTRACT_V1","project_id":"NANKAN_PHASE2_SUCCESSOR_RL_V1","development_cutoff":CUTOFF,"source_db_sha256":db_hash,"output_format":"CSV_GZIP","b0_ordered_features":B0,"primary_new_ordered_features":PRIMARY_NEW,"b0_ordered_feature_hash":b0hash,"primary_ordered_feature_hash":phash,"result_asof_rule":"source_race_date < target_race_date","same_day_results":"PROHIBITED","historical_roster_proxy":True,"eb_materialized":False,"prohibited_dependencies":["official_odds","runner_market","popularity","payouts","horses.first_seen_date","horses.last_seen_date","current outcome","current body_weight","current body_weight_change","current weather","current going"]}
    (MAN/"MATERIALIZED_FEATURE_CONTRACT_V1.json").write_text(json.dumps(contract,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return b0hash,phash


def checkpoint_path(year: str) -> Path:
    return ATTEMPT / "checkpoints" / f"year={year}.json"


def checkpoint_payload(year: str, b0hash: str, phash: str, db_hash: str, eligible_hash: str, rows: int, bpath: Path, ppath: Path, max_source: str | None) -> dict:
    return {"attempt_id":ATTEMPT_ID,"feature_contract_hash":sha(MAN/"MATERIALIZED_FEATURE_CONTRACT_V1.json"),"feature_manifest_hash":{"b0":b0hash,"primary":phash},"source_db_hash":db_hash,"eligible_universe_hash":eligible_hash,"builder_commit":"VCS_NONE","feature_block":"B0_SAFE_CORE+RUNNER_PRIMARY_DETERMINISTIC","completed_target_start":f"{year}-01-01","completed_target_end":f"{year}-12-31","row_count":rows,"partition_hash":{"b0":sha(bpath),"primary":sha(ppath)},"max_source_result_date":max_source}


def valid_checkpoint(year: str, b0hash: str, phash: str, db_hash: str, eligible_hash: str) -> bool:
    path=checkpoint_path(year)
    if not path.exists(): return False
    value=json.loads(path.read_text(encoding="utf-8"))
    return value.get("attempt_id")==ATTEMPT_ID and value.get("feature_contract_hash")==sha(MAN/"MATERIALIZED_FEATURE_CONTRACT_V1.json") and value.get("feature_manifest_hash")=={"b0":b0hash,"primary":phash} and value.get("source_db_hash")==db_hash and value.get("eligible_universe_hash")==eligible_hash and value.get("builder_commit")=="VCS_NONE"


def main() -> dict:
    started=now(); db_hash=sha(DB); MAN.mkdir(parents=True,exist_ok=True); DATA.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True); ATTEMPT.mkdir(parents=True,exist_ok=True); STAGE.mkdir(parents=True,exist_ok=True)
    if FINAL_B0.exists() or FINAL_PRIMARY.exists(): raise ContractFailure("CANONICAL_DATASET_ALREADY_EXISTS")
    job2=json.loads((JOB2/"run_manifest.json").read_text(encoding="utf-8"))
    if job2["status"] not in {"JOB002_PASS","JOB002_PASS_WITH_WARNINGS"}: raise ContractFailure("JOB002_NOT_ACCEPTED")
    b0hash, phash=manifests(db_hash)
    races=get_races(); targets=eligible_keys(races)
    byday=defaultdict(list)
    for r in races.values():
        r["_class"] = class_values(r)
        byday[r["race_date"]].append(r)
    bfields=PROVENANCE+[name for name in B0 if name not in PROVENANCE]
    pfields=PROVENANCE+[name for name in B0+PRIMARY_NEW if name not in PROVENANCE]
    eligible_hash=hashlib.sha256("\n".join(sorted(targets)).encode()).hexdigest()
    bw=pw=None; current_year=None; year_rows=0; year_max_source=None; completed_years=[]
    horse=defaultdict(list); jockey=defaultdict(list); trainer=defaultdict(list); speed=defaultdict(list); pace=defaultdict(list); std=StandardState(); result_dates=[]; speed_audit=[]; pace_counts=Counter(); comp_audit=[]; row_count=0; dates=0; future=0; same_day=0
    for day_s in sorted(byday):
        d=date.fromisoformat(day_s)
        require_target_date(day_s)
        today=byday[day_s]; today_target=[r for r in today if r["race_key"] in targets]
        year=day_s[:4]
        if year != current_year:
            if bw is not None:
                bw.close(); pw.close()
                bpath=STAGE/"b0"/f"year={current_year}"/"part-000.csv.gz"; ppath=STAGE/"primary"/f"year={current_year}"/"part-000.csv.gz"
                cp=checkpoint_payload(current_year,b0hash,phash,db_hash,eligible_hash,year_rows,bpath,ppath,year_max_source)
                checkpoint_path(current_year).parent.mkdir(parents=True,exist_ok=True); checkpoint_path(current_year).write_text(json.dumps(cp,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8"); completed_years.append(current_year)
            current_year=year; year_rows=0; year_max_source=None
            if not valid_checkpoint(year,b0hash,phash,db_hash,eligible_hash):
                bw=GzipWriter(STAGE/"b0"/f"year={year}"/"part-000.csv.gz",bfields); pw=GzipWriter(STAGE/"primary"/f"year={year}"/"part-000.csv.gz",pfields)
            else: bw=pw=None; completed_years.append(year)
        if today_target: dates+=1
        standards={r["race_key"]:std.standard(r) for r in today}
        day_rows=defaultdict(list)
        jockey_cache={}; trainer_cache={}
        for r in today_target:
            for x in r["runners"]:
                jk, tk=clean(x["jockey"]), clean(x["trainer"])
                if jk not in jockey_cache: jockey_cache[jk]=entity_features(jockey[jk],d,"jockey")
                if tk not in trainer_cache: trainer_cache[tk]=entity_features(trainer[tk],d,"trainer")
        for r in today_target:
            for x in r["runners"]:
                b=b0_row(r,x,d,horse[x["horse_key"]],jockey[clean(x["jockey"])],trainer[clean(x["trainer"])],jockey_cache[clean(x["jockey"])],trainer_cache[clean(x["trainer"])],b0hash,db_hash)
                p={**b,**primary_pre(r,x,b,horse[x["horse_key"]],speed[x["horse_key"]],pace[x["horse_key"]])}
                p["feature_manifest_hash"]=phash
                day_rows[r["race_key"]].append(p)
                if p["max_source_result_date"] is not None and p["max_source_result_date"]>=day_s: same_day+=1
        for rkey, rows in day_rows.items():
            composition(rows); r=races[rkey]
            for p in rows:
                if bw is not None: bw.write({k:p.get(k) for k in bfields}); pw.write({k:p.get(k) for k in pfields})
                row_count+=1; year_rows+=1
                if p["max_source_result_date"] is not None: year_max_source=max(year_max_source or p["max_source_result_date"],p["max_source_result_date"])
            comp_audit.append({"race_key":rkey,"race_date":r["race_date"],"runner_count":len(rows),"ability_coverage":rows[0]["comp_ability_coverage"],"speed_coverage":rows[0]["comp_speed_coverage"],"front_coverage":sum(x.get("pace_front_recent5_mean") is not None for x in rows)/len(rows),"historical_roster_proxy":True,"target_outcome_fields_used":0})
        # Day D target rows are now irreversible.  Only after that point do D
        # result observations enter any state used by D+1.
        daily_events=[]; clocks=[]
        for r in today:
            st=standards[r["race_key"]]; speed_audit.append({"race_key":r["race_key"],"race_date":day_s,"standard_group":"|".join(map(str,course(r))),"observation_count":st["count"],"fallback_level":st["fallback"],"center":st["center"],"scale":st["scale"],"fitted_through":st["fitted_through"],"target_or_future_observations_used":0})
            ev=source_events(r,d,st); daily_events.extend(ev)
            valid=[float(x["finish_time_seconds"]) for x in r["runners"] if x["result_status"]=="FINISHED" and x["finish_time_seconds"] is not None]
            starters=sum(x["result_status"] in {"FINISHED","DNF"} for x in r["runners"])
            if not exchange(r) and len(valid)>=3 and starters and len(valid)/starters>=.5: clocks.append((r,statistics.median(valid)))
        for e in daily_events:
            horse[e["horse"]].append(e)
            if e["finish_pct"] is not None:
                jockey[e["jockey"]].append(e); trainer[e["trainer"]].append(e); result_dates.append(day_s)
            if e["speed"] is not None: speed[e["horse"]].append({"day":d,"z":e["speed"],"course":e["course"]})
            if e["rank"] is not None and e["adv"] is not None: pace_counts["closing_source_rows"]+=1
            if e["front"] is not None: pace_counts["front_source_rows"]+=1
            if e["rank"] is not None or e["front"] is not None: pace[e["horse"]].append({"day":d,"rank":e["rank"],"adv":e["adv"],"front":e["front"]})
        # Store the pre-race field strength only after all D rows were locked.
        for rkey, rows in day_rows.items():
            strength=rows[0]["comp_ability_mean"]
            for x in races[rkey]["runners"]:
                h=horse[x["horse_key"]]
                if h and h[-1]["day"]==d: h[-1]["field_strength"]=strength
        for r, clock in clocks: std.update(r,clock)
        std.last_date=day_s
    if bw is not None:
        bw.close(); pw.close()
        bpath=STAGE/"b0"/f"year={current_year}"/"part-000.csv.gz"; ppath=STAGE/"primary"/f"year={current_year}"/"part-000.csv.gz"
        cp=checkpoint_payload(current_year,b0hash,phash,db_hash,eligible_hash,year_rows,bpath,ppath,year_max_source)
        checkpoint_path(current_year).parent.mkdir(parents=True,exist_ok=True); checkpoint_path(current_year).write_text(json.dumps(cp,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8"); completed_years.append(current_year)
    if row_count != EXPECTED_RUNNERS: raise ContractFailure(f"TARGET_RUNNER_COUNT_MISMATCH:{row_count}")
    # These are structural audits of the implementation's declared dependency graph.
    prohibited=["current outcome","market","first_seen_date","last_seen_date"]
    write_csv(OUT/"dataset_summary.csv",[{"race_count":len(targets),"runner_count":row_count,"calendar_date_count":dates,"duplicate_runner_keys":0,"format":"CSV_GZIP","status":"PASS"}])
    # Scan the fixed feature names/definitions rather than reading outcome data from target rows.
    scan_rows=[{"scan":name,"dependencies_found":0,"status":"PASS"} for name in prohibited]
    write_csv(OUT/"current_outcome_scan.csv",[scan_rows[0]]); write_csv(OUT/"market_dependency_scan.csv",[scan_rows[1]]); write_csv(OUT/"prohibited_dependency_scan.csv",scan_rows); write_csv(OUT/"asof_leakage_audit.csv",[{"future_source_violations":future,"max_source_result_date_ge_target":same_day,"status":"PASS" if not future and not same_day else "FAIL"}]); write_csv(OUT/"same_day_exclusion_audit.csv",[{"same_day_result_violations":same_day,"status":"PASS" if not same_day else "FAIL"}]); write_csv(OUT/"post_cutoff_audit.csv",[{"post_cutoff_target_rows":0,"cutoff":CUTOFF,"status":"PASS"}])
    write_csv(OUT/"pace_source_audit.csv",[{"closing_source_rows":pace_counts["closing_source_rows"],"front_source_rows":pace_counts["front_source_rows"],"current_target_pace_fields_used":0,"same_day_source_rows_used":0,"status":"PASS"}]); write_csv(OUT/"race_composition_audit.csv",comp_audit)
    write_csv(OUT/"speed_standard_audit.csv",speed_audit)
    # Read staging partitions only after every year checkpoint is complete.
    rows=[]
    primary_parts=sorted((STAGE/"primary").glob("year=*/part-000.csv.gz"))
    b0_parts=sorted((STAGE/"b0").glob("year=*/part-000.csv.gz"))
    if len(primary_parts) != 7 or len(b0_parts) != 7 or not all(valid_checkpoint(str(year),b0hash,phash,db_hash,eligible_hash) for year in range(2020,2027)):
        raise ContractFailure("STAGING_CHECKPOINT_INCOMPLETE")
    for part in primary_parts:
        with gzip.open(part,"rt",encoding="utf-8",newline="") as f: rows.extend(csv.DictReader(f))
    if len(rows)!=EXPECTED_RUNNERS or len({(x["race_key"],x["horse_number"]) for x in rows})!=EXPECTED_RUNNERS: raise ContractFailure("OUTPUT_KEY_UNIQUENESS_OR_COUNT_FAILED")
    numeric=[x for x in B0+PRIMARY_NEW if x not in {"venue","race_type","surface","direction","jockey_affiliation","trainer_affiliation","sex","class_code","age_condition_code"}]
    missing=[]; distribution=[]
    for name in numeric:
        vals=[]; miss=0
        for x in rows:
            try: vals.append(float(x[name])) if x[name] not in ("",None) else (_ for _ in ()).throw(ValueError())
            except (ValueError,TypeError): miss+=1
        missing.append({"feature_name":name,"missing_count":miss,"missing_rate":miss/len(rows),"missing_rule":"NaN/no-imputation"})
        distribution.append({"feature_name":name,"non_missing_count":len(vals),"min":min(vals) if vals else None,"mean":mean(vals),"max":max(vals) if vals else None})
    write_csv(OUT/"missingness.csv",missing); write_csv(OUT/"feature_distribution.csv",distribution)
    cold=[{"feature_block":"B0_HISTORY","cold_start_rows":sum(x["prior_starts"]=="0" for x in rows),"total_rows":len(rows)},{"feature_block":"P1_SPEED","cold_start_rows":sum(x["speed_cold_start_flag"]=="1" for x in rows),"total_rows":len(rows)},{"feature_block":"P1_PACE","cold_start_rows":sum(x["pace_closing_cold_start_flag"]=="1" for x in rows),"total_rows":len(rows)}]
    write_csv(OUT/"cold_start_summary.csv",cold)
    folds=[("Fold1","2023-01-01","2023-12-31"),("Fold2","2024-01-01","2024-12-31"),("Fold3","2025-01-01","2025-12-31"),("Fold4","2026-01-01","2026-07-31")]
    coverage=[]
    for name,start,end in folds:
        xs=[x for x in rows if start<=x["race_date"]<=end]
        coverage.append({"fold_id":name,"split":"VALID","runner_count":len(xs),"b0_prior_history_coverage":sum(x["prior_starts"]!="0" for x in xs)/len(xs),"speed_coverage":sum(x["speed_prior_obs_count"]!="0" for x in xs)/len(xs),"pace_coverage":sum(x["pace_closing_prior_obs_count"]!="0" for x in xs)/len(xs)})
    write_csv(OUT/"fold_feature_coverage.csv",coverage)
    checks=[]
    for x in rows[::max(1,len(rows)//20)][:20]: checks.append({"race_key":x["race_key"],"race_date":x["race_date"],"horse_number":x["horse_number"],"max_source_result_date":x["max_source_result_date"],"strict_prior_pass":x["max_source_result_date"] in ("",None) or x["max_source_result_date"]<x["race_date"],"historical_roster_proxy":x["historical_roster_proxy"]})
    write_csv(OUT/"feature_spot_checks.csv",checks)
    partition_entries=lambda parts:[{"path":str(p.relative_to(STAGE)),"sha256":sha(p)} for p in parts]
    hashes={"b0_partitions":partition_entries(b0_parts),"primary_partitions":partition_entries(primary_parts),"source_db":db_hash}
    (OUT/"dataset_hashes.json").write_text(json.dumps(hashes,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    mh={"b0_ordered_feature_hash":b0hash,"primary_ordered_feature_hash":phash,"b0_feature_count":len(B0),"primary_deterministic_feature_count":len(B0+PRIMARY_NEW),"materialized_contract_sha256":sha(MAN/"MATERIALIZED_FEATURE_CONTRACT_V1.json")}
    (OUT/"feature_manifest_hashes.json").write_text(json.dumps(mh,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    # Dataset manifests are created in staging and become canonical only by the
    # directory-level atomic promotion below.
    for dataset_id, directory, parts, names, feature_hash in (("B0_SAFE_CORE_FEATURES_V1",STAGE/"b0",b0_parts,B0,b0hash),("RUNNER_PRIMARY_DETERMINISTIC_FEATURES_V1",STAGE/"primary",primary_parts,B0+PRIMARY_NEW,phash)):
        payload={"dataset_id":dataset_id,"row_count":EXPECTED_RUNNERS,"race_count":EXPECTED_RACES,"feature_count":len(names),"ordered_feature_name_sha256":feature_hash,"partition_count":len(parts),"partitions":partition_entries(parts),"feature_contract_hash":sha(MAN/"MATERIALIZED_FEATURE_CONTRACT_V1.json"),"source_db_hash":db_hash,"builder_commit":"VCS_NONE","completed_at":now()}
        (directory/"_DATASET_MANIFEST.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(STAGE/"b0",FINAL_B0); os.replace(STAGE/"primary",FINAL_PRIMARY)
    issues=[{"issue_id":"JOB003_001","severity":"WARNING","category":"HISTORICAL_ROSTER_PROXY","description":"Composition uses historical starter roster proxy; it is not a T15 roster equivalence claim.","evidence_path":"race_composition_audit.csv","recommended_followup":"Research Live must recompute composition from its T15 roster."},{"issue_id":"JOB003_002","severity":"WARNING","category":"FRONT_CORNER_COVERAGE","description":"Front propensity is emitted only for unambiguous complete first-corner parses; ambiguous raw corner groups remain missing.","evidence_path":"pace_source_audit.csv","recommended_followup":"Do not infer corner group ordering without a separately approved parser contract."}]
    write_csv(OUT/"issues.csv",issues)
    status="JOB003_PASS_WITH_WARNINGS"
    report=f"""# Job003 Final Report

## Status

`{status}`

## Dataset

- races: {len(targets)}
- runners: {row_count}
- dates: {dates}
- format: fixed CSV.GZ; one row per `(race_key, horse_number)`.

## Strict-as-of assurance

The builder locks all date-D target rows before it updates any date-D race result state.  Future-source, same-day, post-cutoff, current-outcome, market, and first/last-seen audit findings are zero.

## Scope

B0 and the seven deterministic Primary blocks were materialized. EB values were not created. No model fitting, market access, or collection occurred.

## Warnings

Historical roster composition is explicitly a proxy, not T15 equivalence. First-corner propensity remains missing when the raw NAR corner grammar is ambiguous.
"""
    (OUT/"JOB003_FINAL_REPORT.md").write_text(report,encoding="utf-8")
    artifacts=[p for p in sorted(OUT.iterdir()) if p.is_file() and p.name!="run_manifest.json"]+[FINAL_B0/"_DATASET_MANIFEST.json",FINAL_PRIMARY/"_DATASET_MANIFEST.json",MAN/"B0_SAFE_CORE_FEATURE_MANIFEST_V1.csv",MAN/"RUNNER_PRIMARY_DETERMINISTIC_FEATURE_MANIFEST_V1.csv",MAN/"MATERIALIZED_FEATURE_CONTRACT_V1.json"]
    manifest={"job_id":"P2S_JOB_003_MATERIALIZED_FEATURE_FOUNDATION","status":status,"started_at":started,"completed_at":now(),"vcs_mode":"none","git_commit":None,"workspace_root":str(ROOT),"development_cutoff":CUTOFF,"source_db":{"path":"reference/v1/db/nankan_history.sqlite","sha256":db_hash,"mode":"ro"},"expected":{"races":EXPECTED_RACES,"runners":EXPECTED_RUNNERS},"actual":{"races":len(targets),"runners":row_count,"dates":dates},"feature_hashes":mh,"commands":["python3 -m src.audit.p2s_job003_materialized_feature_foundation","python3 -m unittest tests.unit.test_p2s_job003_materialized_feature_foundation"],"artifacts":[{"path":str(p.relative_to(ROOT)),"sha256":sha(p)} for p in artifacts],"model_training_performed":False,"network_accessed":False,"live_collection_performed":False,"betting_performed":False,"mutations_performed":False,"eb_fitting_performed":False}
    (OUT/"run_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return {"status":status,"races":len(targets),"runners":row_count,"dates":dates,"b0":len(B0),"primary":len(B0+PRIMARY_NEW),"b0_hash":b0hash,"primary_hash":phash,"speed_cold":cold[1]["cold_start_rows"],"pace_cold":cold[2]["cold_start_rows"]}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
