"""R13-A: compile retained R4 official raw into M01-compatible primitives.

This small rebuildable compiler never fetches a page and preserves the
R4-approved identity/outcome semantics already stored in the source delta.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from src.audit import p2_m01_build_history_context as m01
from src.features.course_direction import resolve_current_target_direction
from src.ingestion.adapters import nankan_official as official

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "db" / "p2_live_history_delta.sqlite"
BASE = ROOT / "db" / "p2_history_context.sqlite"
STAGING = ROOT / "db" / ".p2_live_history_normalized_delta.tmp.sqlite"
AUDIT = ROOT / "audit" / "data" / "p2_m12b_r13"
EXPECTED_RACES, EXPECTED_RUNNERS, EXPECTED_HORSES = 204, 2130, 2089


class PrimitiveUnavailable(RuntimeError):
    def __init__(self, field: str, race_key: str, detail: str) -> None:
        super().__init__(f"BLOCKED_ON_NORMALIZED_DELTA_PRIMITIVE_{field}:{race_key}:{detail}")
        self.field, self.race_key, self.detail = field, race_key, detail


def _decode(path: str) -> str:
    return official.decode_html((ROOT / path).read_bytes(), None)


def _race_type_raw(html: str, race_key: str) -> str:
    root = official.parse_html(html)
    for node in official.iter_nodes(root, "span"):
        if "nk23_c-tab1__texts__gr" not in node.attrs.get("class", ""):
            continue
        for child in official.iter_nodes(node, "span"):
            match = re.fullmatch(r"（([^（）]+)）", official.node_text(child).strip())
            if match and match.group(1):
                return match.group(1)
    raise PrimitiveUnavailable("race_type_raw", race_key, "explicit official card category token absent")


def _card_static_rows(html: str, identity: dict[str, Any]) -> dict[int, dict[str, Any]]:
    pedigree = {row["horse_number"]: dict(row) for row in official.parse_official_pedigree_identity_card(html, identity=identity)}
    root = official.parse_html(html)
    for row in official.iter_nodes(root, "tr"):
        cells = [cell for cell in official.direct_cells(row) if cell.tag == "td"]
        target = [cell for cell in cells if official._class_has(cell, "pr-umaName-textRound")]
        numbers = [int(cell.attrs["data-num"]) for cell in cells if re.fullmatch(r"\d+", cell.attrs.get("data-num", ""))]
        if len(target) != 1 or len(numbers) != 1 or numbers[0] not in pedigree:
            continue
        details = [official.node_text(node) for node in official.iter_nodes(target[0], "p") if official._class_has(node, "nk23_u-text10")]
        match = re.match(r"([牡牝セ])\d+\s+([^\s]+)", details[0] if details else "")
        pedigree[numbers[0]]["sex"] = match.group(1) if match else None
        pedigree[numbers[0]]["color"] = match.group(2) if match else None
    return pedigree


def _finish_seconds(raw: str | None, result_status: str, race_key: str, horse_number: int) -> float | None:
    if raw is None:
        return None  # approved NONSTARTER / STARTER_NO_VALID_FINISH NULL semantics
    match = re.fullmatch(r"(\d+):(\d{2})\.(\d)", raw.strip())
    if match:
        parsed = m01.finish_seconds(f"{match.group(1)}{match.group(2)}{match.group(3)}")
        if parsed is not None:
            return parsed
    # Official sub-minute displays are e.g. ``59.7``; this is the same M01
    # compact raw representation ``597``, not a new timing interpretation.
    match = re.fullmatch(r"(\d{1,2})\.(\d)", raw.strip())
    if match:
        parsed = m01.finish_seconds(f"{int(match.group(1)):02d}{match.group(2)}")
        if parsed is not None:
            return parsed
    raise PrimitiveUnavailable("finish_time_seconds", race_key, f"horse={horse_number};raw={raw!r};status={result_status}")


def _create_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE horses(horse_identity_key TEXT PRIMARY KEY,horse_name_exact TEXT NOT NULL,birth_date TEXT NOT NULL,sex TEXT,color TEXT,sire TEXT,dam TEXT,damsire TEXT,identity_method TEXT NOT NULL,identity_version TEXT NOT NULL,identity_quality TEXT NOT NULL,rename_link_status TEXT NOT NULL,first_observed_race_date TEXT NOT NULL,UNIQUE(horse_name_exact,birth_date));
    CREATE TABLE races(race_key TEXT PRIMARY KEY,race_date TEXT NOT NULL,venue TEXT NOT NULL,venue_class TEXT NOT NULL,race_number INTEGER NOT NULL,post_time TEXT,race_type_raw TEXT NOT NULL,race_name TEXT,conditions_raw TEXT,surface TEXT NOT NULL,direction TEXT NOT NULL,distance_m INTEGER NOT NULL,weather TEXT,going TEXT,field_size INTEGER NOT NULL,final_4f REAL,final_3f REAL,lap_times_json TEXT,result_capture_id TEXT NOT NULL,card_capture_path TEXT NOT NULL,UNIQUE(race_date,venue,race_number));
    CREATE TABLE race_runners(race_key TEXT NOT NULL REFERENCES races(race_key),horse_identity_key TEXT NOT NULL REFERENCES horses(horse_identity_key),frame_number INTEGER,horse_number INTEGER NOT NULL,jockey TEXT,trainer TEXT,assigned_weight REAL,body_weight INTEGER,body_weight_change INTEGER,finish_position_raw TEXT,finish_position INTEGER,result_status TEXT NOT NULL,finish_time_raw TEXT,finish_time_seconds REAL,margin_raw TEXT,last_3f REAL,PRIMARY KEY(race_key,horse_number));
    CREATE TABLE build_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    """)


def _logical_hash(path: Path) -> str:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True); digest = hashlib.sha256()
    try:
        for table, order in (("horses", "horse_identity_key"), ("races", "race_key"), ("race_runners", "race_key,horse_number")):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            for row in con.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                digest.update(json.dumps(dict(zip(cols, row)), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()); digest.update(b"\n")
    finally:
        con.close()
    return digest.hexdigest()


def _load_source(source: Path = SOURCE, *, expected_counts: tuple[int, int, int] | None = (EXPECTED_RACES, EXPECTED_RUNNERS, EXPECTED_HORSES)) -> tuple[list[sqlite3.Row], dict[str, sqlite3.Row], dict[str, sqlite3.Row], dict[str, list[sqlite3.Row]], dict[str, sqlite3.Row]]:
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    races = con.execute("SELECT * FROM races ORDER BY race_date,venue,race_number").fetchall()
    captures = con.execute("SELECT * FROM source_captures").fetchall()
    runners = con.execute("SELECT * FROM race_runners ORDER BY race_key,horse_number").fetchall()
    horses = {r["horse_identity_key"]: r for r in con.execute("SELECT * FROM horses")}; con.close()
    if expected_counts is not None and (len(races), len(runners), len(horses)) != expected_counts:
        raise RuntimeError(f"R13_SOURCE_ACCOUNTING:{len(races)}:{len(runners)}:{len(horses)}")
    # The official card/result URLs retain the same 16-digit race id.  Use it
    # only to pair already-retained pages; ``resolve_race`` below remains the
    # authoritative identity validation and avoids a second full-card parse.
    cards = {official.url_identity(r["source_url"])["race_id_raw"]: r for r in captures if r["source_type"] == "OFFICIAL_CARD"}
    by_capture = {r["capture_id"]: r for r in captures}; runners_by_race: dict[str, list[sqlite3.Row]] = {}
    for runner in runners: runners_by_race.setdefault(runner["race_key"], []).append(runner)
    return races, cards, by_capture, runners_by_race, horses


def _base_horses() -> dict[str, sqlite3.Row]:
    con = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    rows = {r["horse_identity_key"]: r for r in con.execute("SELECT * FROM horses")}; con.close(); return rows


def _write_horse_coverage(output: Path, source_horses: dict[str, sqlite3.Row], base_horses: dict[str, sqlite3.Row]) -> None:
    """Persist the one-time 105-horse static primitive audit from saved sources."""
    con = sqlite3.connect(f"file:{output}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    rows: list[dict[str, Any]] = []
    for horse in con.execute("SELECT * FROM horses ORDER BY horse_identity_key"):
        source = source_horses[horse["horse_identity_key"]]
        is_new = horse["horse_identity_key"] not in base_horses
        for field in ("horse_name_exact", "birth_date", "sex", "color", "sire", "dam", "damsire"):
            # Birth date/identity were admitted by R4 only through the approved
            # direct official-detail route or R7 exact pedigree fallback; all
            # nonidentity static fields for a new horse are its saved card row.
            source_label = "BASE_CANONICAL_MASTER" if not is_new else ("R4_APPROVED_DETAIL_OR_R7_IDENTITY" if field in {"horse_name_exact", "birth_date"} else "SAVED_OFFICIAL_PRE_RACE_CARD")
            rows.append({"horse_identity_key":horse["horse_identity_key"],"horse_name_exact":horse["horse_name_exact"],"new_post_cutoff_horse":is_new,"required_field":field,"saved_source":source_label,"resolved":horse[field] is not None,"null_legally_allowed":False,"unresolved_reason":"" if horse[field] is not None else "SAVED_SOURCE_FIELD_ABSENT"})
        rows.append({"horse_identity_key":horse["horse_identity_key"],"horse_name_exact":horse["horse_name_exact"],"new_post_cutoff_horse":is_new,"required_field":"official_horse_id","saved_source":"R4_OFFICIAL_IDENTITY_PROVENANCE","resolved":source["official_horse_id"] is not None,"null_legally_allowed":True,"unresolved_reason":"" if source["official_horse_id"] is not None else "NOT_REQUIRED_BY_M01_OR_CONSUMER_CONTRACT"})
    con.close()
    with (AUDIT / "horse_primitive_source_coverage.csv").open("w", encoding="utf-8", newline="") as handle:
        fields=["horse_identity_key","horse_name_exact","new_post_cutoff_horse","required_field","saved_source","resolved","null_legally_allowed","unresolved_reason"]
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def compile_primitives(output: Path = STAGING, limit: int | None = None, start: int = 0, resume: bool = False, *, source: Path = SOURCE, expected_counts: tuple[int, int, int] | None = (EXPECTED_RACES, EXPECTED_RUNNERS, EXPECTED_HORSES), race_keys: set[str] | None = None) -> dict[str, Any]:
    if output.exists() and not resume: output.unlink()
    races, cards, captures, runners_by_race, source_horses = _load_source(source, expected_counts=expected_counts)
    selected = [race for race in races if race_keys is None or race["race_key"] in race_keys]
    selected = selected[start:start + limit] if limit is not None else selected[start:]
    base_horses = _base_horses(); con = sqlite3.connect(output)
    if not resume: _create_schema(con)
    coverage: list[dict[str, Any]] = []; seen: dict[str, tuple[Any, ...]] = {}
    try:
        con.execute("BEGIN IMMEDIATE")
        for race in selected:
            result_capture = captures[race["result_capture_id"]]
            race_id = official.url_identity(result_capture["source_url"])["race_id_raw"]
            card = cards.get(race_id)
            if card is None: raise PrimitiveUnavailable("official_card_raw", race["race_key"], "matching_cards=0")
            card_html = _decode(card["raw_archive_path"]); card_identity = official.resolve_race(card["source_url"], card_html)
            if (card_identity["race_date"], card_identity["venue"], card_identity["race_number"]) != (race["race_date"], race["venue"], race["race_number"]): raise PrimitiveUnavailable("race_identity", race["race_key"], "retained card identity conflict")
            card_rows = _card_static_rows(card_html, card_identity); result = official.parse_history_result_fields(_decode(result_capture["raw_archive_path"]), identity=card_identity)
            result_rows = {r["horse_number"]: r for r in result["runners"]}; direction = resolve_current_target_direction(venue=race["venue"], distance_m=card_identity["distance_m"]); race_type = _race_type_raw(card_html, race["race_key"])
            fields = {"surface": card_identity["surface"], "direction": direction["direction"], "race_type_raw": race_type, "distance_m": card_identity["distance_m"], "field_size": card_identity["field_size"]}
            for field, value in fields.items():
                coverage.append({"race_key":race["race_key"],"field":field,"required_by":"V1|P2_CLASS|P2_SPD|P2_PACE","source":"OFFICIAL_CARD" if field != "direction" else direction["direction_source_status"],"resolved":value is not None,"value":value})
                if value is None: raise PrimitiveUnavailable(field, race["race_key"], "approved retained source absent")
            con.execute("INSERT INTO races VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (race["race_key"],race["race_date"],race["venue"],m01.venue_class(race["venue"]),race["race_number"],card_identity["scheduled_post_time_local"],race_type,card_identity["race_name"],card_identity["conditions_raw"],card_identity["surface"],direction["direction"],card_identity["distance_m"],result["weather"],result["going"],card_identity["field_size"],result["final_4f"],result["final_3f"],json.dumps(result["lap_times"],ensure_ascii=False),race["result_capture_id"],card["raw_archive_path"]))
            source_rows = runners_by_race[race["race_key"]]
            if len(source_rows) != len(result_rows): raise PrimitiveUnavailable("race_runners", race["race_key"], f"R4={len(source_rows)};result={len(result_rows)}")
            for runner in source_rows:
                number=runner["horse_number"]; parsed=result_rows.get(number); card_row=card_rows.get(number)
                if parsed is None or card_row is None: raise PrimitiveUnavailable("race_runner_card_or_result",race["race_key"],f"horse={number}")
                source_horse=source_horses[runner["horse_identity_key"]]
                if card_row["horse_name_exact"] != source_horse["horse_name_exact"]: raise PrimitiveUnavailable("horse_name_exact",race["race_key"],f"horse={number};card/source conflict")
                base=base_horses.get(runner["horse_identity_key"]); static=(base["sex"],base["color"],base["sire"],base["dam"],base["damsire"]) if base else (card_row.get("sex"),card_row.get("color"),card_row.get("sire"),card_row.get("dam"),card_row.get("damsire"))
                horse=(runner["horse_identity_key"],source_horse["horse_name_exact"],source_horse["birth_date"],*static,"P2_HORSE_IDENTITY_V1","P2_HORSE_IDENTITY_V1","EXACT_OFFICIAL_PRE_RACE","NOT_APPLICABLE",race["race_date"])
                old=seen.get(runner["horse_identity_key"])
                if old is None and resume:
                    persisted=con.execute("SELECT horse_identity_key,horse_name_exact,birth_date,sex,color,sire,dam,damsire,identity_method,identity_version,identity_quality,rename_link_status,first_observed_race_date FROM horses WHERE horse_identity_key=?",(runner["horse_identity_key"],)).fetchone()
                    if persisted is not None:
                        old=tuple(persisted)
                if old is not None and old[1:8] != horse[1:8]: raise PrimitiveUnavailable("horse_static_attribute_conflict",race["race_key"],f"horse={number}")
                if old is None: seen[runner["horse_identity_key"]]=horse; con.execute("INSERT INTO horses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",horse)
                con.execute("INSERT INTO race_runners VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(race["race_key"],runner["horse_identity_key"],runner["frame_number"],number,runner["jockey"],runner["trainer"],runner["assigned_weight"],runner["body_weight"],runner["body_weight_change"],runner["finish_position_raw"],parsed["finish_position"],parsed["result_status"],parsed["finish_time_raw"],_finish_seconds(parsed["finish_time_raw"],parsed["result_status"],race["race_key"],number),parsed["margin_raw"],parsed["last_3f"]))
        con.execute("INSERT INTO build_metadata(key,value) VALUES('compiler','P2_M12B_R13_A_M01_PRIMITIVES') ON CONFLICT(key) DO UPDATE SET value=excluded.value"); con.commit()
    except Exception:
        con.rollback(); con.close(); raise
    quick=con.execute("PRAGMA quick_check").fetchone()[0]; fk=con.execute("PRAGMA foreign_key_check").fetchall(); counts={t:con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("horses","races","race_runners")}; con.close()
    AUDIT.mkdir(parents=True,exist_ok=True)
    coverage_path = AUDIT/"race_primitive_source_coverage.csv"
    mode = "a" if resume and coverage_path.exists() else "w"
    with coverage_path.open(mode,encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=["race_key","field","required_by","source","resolved","value"])
        if mode == "w": writer.writeheader()
        writer.writerows(coverage)
    source_runner_count = sum(len(values) for values in runners_by_race.values())
    if counts["races"] == len(races) and counts["race_runners"] == source_runner_count:
        _write_horse_coverage(output, source_horses, base_horses)
    return {"source_races":len(selected),"source_runners":sum(len(runners_by_race[r["race_key"]]) for r in selected),"normalized_horses":counts["horses"],"normalized_races":counts["races"],"normalized_runners":counts["race_runners"],"quick_check":quick,"foreign_key_rows":len(fk),"logical_hash":_logical_hash(output)}


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument("--fixture",action="store_true");parser.add_argument("--output",type=Path,default=STAGING);parser.add_argument("--start",type=int,default=0);parser.add_argument("--limit",type=int);parser.add_argument("--resume",action="store_true");args=parser.parse_args()
    count=5 if args.fixture else args.limit
    print(json.dumps(compile_primitives(args.output,count,args.start,args.resume),ensure_ascii=False,sort_keys=True))


if __name__ == "__main__": main()
