"""Build the P7 official-ID crosswalk for frozen V1 person categories.

Only retained ``OFFICIAL_CARD`` captures are read.  The result page is never
used as an identity source: a delta runner is paired to its card by the
already-audited race key and horse number, then retains its original raw
result-side display separately from the V1 compatibility token.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official

ROOT = Path(__file__).resolve().parents[2]
RAW_DELTA = ROOT / "db" / "p2_live_history_delta.sqlite"
NORMALIZED_DELTA = ROOT / "db" / "p2_live_history_normalized_delta.sqlite"
BASE = ROOT / "db" / "p2_history_context.sqlite"
AUDIT = ROOT / "audit" / "data" / "p2_m12b"
PREPROCESSING = ROOT / "models" / "development" / "dev_live_v1" / "preprocessing.json"

LEADING_MARK = re.compile(r"^(\[[^\[\]]+\]|[▲△◇☆])")


def _key(race_date: str, venue: str, race_number: int) -> str:
    return f"P2_RACE_V1::{race_date}\x1f{venue}\x1f{race_number}"


def _read_card_contexts(raw_delta: Path = RAW_DELTA) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, dict[str, set[str]]], list[dict[str, Any]]]:
    con = sqlite3.connect(f"file:{raw_delta}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    captures = con.execute("SELECT capture_id,source_url,raw_archive_path FROM source_captures WHERE source_type='OFFICIAL_CARD' ORDER BY source_url").fetchall()
    con.close()
    by_runner: dict[tuple[str, int], dict[str, Any]] = {}
    by_person: dict[str, dict[str, set[str]]] = {
        "jockey": defaultdict(set), "trainer": defaultdict(set),
    }
    rows: list[dict[str, Any]] = []
    for capture in captures:
        html = official.decode_html((ROOT / capture["raw_archive_path"]).read_bytes())
        race = official.resolve_race(capture["source_url"], html)
        race_key = _key(race["race_date"], race["venue"], race["race_number"])
        context = official.parse_official_card_person_category_context(html, identity=race)
        for horse_number, people in context.items():
            key = (race_key, horse_number)
            if key in by_runner:
                raise RuntimeError(f"P7_CARD_RACE_RUNNER_DUPLICATE:{race_key}:{horse_number}")
            record: dict[str, Any] = {"card_capture_id": capture["capture_id"], "card_raw_path": capture["raw_archive_path"]}
            for person_type, item in people.items():
                record[person_type] = item
                by_person[person_type][item["official_person_id"]].add(
                    f"{item['registered_person_name']}\x1f{item['v1_legacy_token']}"
                )
            by_runner[key] = record
            rows.append({"race_key": race_key, "horse_number": horse_number, **record})
    return by_runner, by_person, rows


def _base_tokens() -> dict[str, set[str]]:
    con = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
    try:
        return {
            person_type: {row[0] for row in con.execute(f"SELECT DISTINCT {person_type} FROM race_runners WHERE {person_type} IS NOT NULL")}
            for person_type in ("jockey", "trainer")
        }
    finally:
        con.close()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build(*, raw_delta: Path = RAW_DELTA, normalized_delta: Path = NORMALIZED_DELTA) -> dict[str, Any]:
    AUDIT.mkdir(parents=True, exist_ok=True)
    card_by_runner, person_values, _ = _read_card_contexts(raw_delta)
    base_tokens = _base_tokens()
    crosswalk_rows: list[dict[str, Any]] = []
    approved: dict[str, dict[str, dict[str, str]]] = {"jockey": {}, "trainer": {}}
    for person_type, ids in person_values.items():
        for person_id, values in sorted(ids.items()):
            parsed = [value.split("\x1f", 1) for value in sorted(values)]
            registered = {pair[0] for pair in parsed}; tokens = {pair[1] for pair in parsed}
            status = "PASS"
            if len(registered) != 1 or len(tokens) != 1:
                status = "BLOCK_OFFICIAL_PERSON_ID_NONUNIQUE_CARD_DISPLAY"
            token = next(iter(tokens)) if len(tokens) == 1 else ""
            if status == "PASS" and token not in base_tokens[person_type]:
                # The official-ID/card evidence is still resolved.  This is a
                # genuinely new person category; the frozen preprocessor owns
                # its existing __UNKNOWN__ mapping (code 1).
                status = "PASS_UNSEEN_MODEL_UNKNOWN"
            crosswalk_rows.append({
                "person_type": person_type, "official_person_id": person_id,
                "registered_person_name": "|".join(sorted(registered)),
                "V1_legacy_token": "|".join(sorted(tokens)),
                "card_evidence_count": len(parsed),
                "base_token_exact_present": token in base_tokens[person_type],
                "status": status,
            })
            if status in {"PASS", "PASS_UNSEEN_MODEL_UNKNOWN"}:
                approved[person_type][person_id] = {
                    "registered_person_name": next(iter(registered)), "v1_legacy_token": token,
                }
    _write_csv(AUDIT / "p7_v1_person_category_crosswalk.csv", list(crosswalk_rows[0]), crosswalk_rows)
    blocked_crosswalk = [row for row in crosswalk_rows if row["status"] not in {"PASS", "PASS_UNSEEN_MODEL_UNKNOWN"}]
    if blocked_crosswalk:
        raise RuntimeError(f"BLOCK_V1_PERSON_CATEGORY_CROSSWALK:{blocked_crosswalk[0]['person_type']}:{blocked_crosswalk[0]['official_person_id']}:{blocked_crosswalk[0]['status']}")

    raw = sqlite3.connect(f"file:{raw_delta}?mode=ro", uri=True)
    raw.row_factory = sqlite3.Row
    runner_rows = raw.execute("SELECT rr.*,r.race_date,r.venue,r.race_number FROM race_runners rr JOIN races r ON r.race_key=rr.race_key ORDER BY r.race_date,r.venue,r.race_number,rr.horse_number").fetchall()
    raw.close()
    contexts: list[dict[str, Any]] = []
    annotations: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    for runner in runner_rows:
        race_key = runner["race_key"]; horse_number = int(runner["horse_number"])
        card = card_by_runner.get((race_key, horse_number))
        if card is None:
            unresolved.append({"race_key": race_key, "horse_number": horse_number, "reason": "OFFICIAL_CARD_CONTEXT_MISSING"})
            continue
        record: dict[str, Any] = {"race_key": race_key, "horse_number": horse_number, "card_capture_id": card["card_capture_id"], "card_raw_path": card["card_raw_path"]}
        for person_type in ("jockey", "trainer"):
            item = card[person_type]
            approved_item = approved[person_type].get(item["official_person_id"])
            raw_display = runner[person_type]
            if approved_item is None or approved_item["v1_legacy_token"] != item["v1_legacy_token"]:
                unresolved.append({"race_key": race_key, "horse_number": horse_number, "person_type": person_type, "raw_display": raw_display, "official_person_id": item["official_person_id"], "reason": "OFFICIAL_ID_CROSSWALK_UNRESOLVED"})
                continue
            record.update({
                f"{person_type}_raw_display": raw_display,
                f"{person_type}_official_id": item["official_person_id"],
                f"{person_type}_registered_name": item["registered_person_name"],
                f"{person_type}_v1_token": item["v1_legacy_token"],
            })
            mark = LEADING_MARK.match(raw_display or "")
            annotations[(person_type, mark.group(1) if mark else "")].append({
                "race_key": race_key, "horse_number": horse_number, "raw_display": raw_display,
                "official_person_id": item["official_person_id"], "registered_person_name": item["registered_person_name"],
                "V1_legacy_token": item["v1_legacy_token"],
            })
        contexts.append(record)
    if unresolved:
        raise RuntimeError(f"BLOCK_V1_PERSON_CATEGORY_UNRESOLVED:{unresolved[0]}")
    if len(contexts) != len(runner_rows):
        raise RuntimeError(f"BLOCK_V1_PERSON_CATEGORY_CONTEXT_COUNT:{len(contexts)}:{len(runner_rows)}")

    annotation_rows: list[dict[str, Any]] = []
    for (person_type, mark), values in sorted(annotations.items()):
        annotation_rows.append({
            "person_type": person_type, "leading_annotation": mark or "NONE", "runner_count": len(values),
            "race_count": len({v['race_key'] for v in values}), "example_race": values[0]["race_key"],
            "example_raw_display": values[0]["raw_display"], "example_official_person_id": values[0]["official_person_id"],
            "registered_person_name": values[0]["registered_person_name"], "V1_legacy_token": values[0]["V1_legacy_token"],
            "resolution": "OFFICIAL_ID_CARD_CONTEXT_NO_STRING_STRIPPING",
        })
    _write_csv(AUDIT / "p7_v1_person_annotation_vocabulary.csv", list(annotation_rows[0]), annotation_rows)

    # A V1 text token remains a model category, not a person identity.  The
    # frozen DEV-LIVE-V1 preprocessor owns the only permitted unseen handling.
    preprocessing = json.loads(PREPROCESSING.read_text(encoding="utf-8"))
    model_rows: list[dict[str, Any]] = []
    for person_type in ("jockey", "trainer"):
        category_map = preprocessing["category_maps"][f"V1__{person_type}"]
        for person_id, item in sorted(approved[person_type].items()):
            token = item["v1_legacy_token"]
            model_rows.append({
                "person_type": person_type, "official_person_id": person_id,
                "V1_legacy_token": token, "model_category_code": category_map.get(token, 1),
                "model_category_status": "SEEN" if token in category_map else "UNSEEN_MAPS_TO___UNKNOWN__",
                "preprocessing_contract": "FoldSafePreprocessor.transform:get(token,1)",
            })
    _write_csv(AUDIT / "p7_v1_person_model_category_audit.csv", list(model_rows[0]), model_rows)
    example_names = ("町田直希", "原優介", "杉浦健太", "小野俊斗")
    example_rows = [
        {
            "person_type": person_type, "raw_display": context[f"{person_type}_raw_display"],
            "official_person_id": context[f"{person_type}_official_id"],
            "registered_person_name": context[f"{person_type}_registered_name"],
            "V1_legacy_token": context[f"{person_type}_v1_token"],
            "resolution_method": "EXACT_OFFICIAL_PERSON_ID_CARD_CONTEXT",
            "race_key": context["race_key"], "horse_number": context["horse_number"],
        }
        for context in contexts for person_type in ("jockey", "trainer")
        if context[f"{person_type}_registered_name"] in example_names
    ]
    _write_csv(AUDIT / "p7_v1_person_required_examples.csv", list(example_rows[0]), example_rows)
    collision_rows: list[dict[str, Any]] = []
    for person_type in ("jockey", "trainer"):
        token_ids: defaultdict[str, set[str]] = defaultdict(set)
        for person_id, item in approved[person_type].items():
            token_ids[item["v1_legacy_token"]].add(person_id)
        for token, ids in sorted(token_ids.items()):
            if len(ids) > 1:
                collision_rows.append({"person_type": person_type, "V1_legacy_token": token, "official_person_ids": "|".join(sorted(ids)), "status": "LEGACY_CATEGORY_COLLISION_PRESERVED"})
    _write_csv(AUDIT / "p7_v1_person_legacy_category_collisions.csv", ["person_type", "V1_legacy_token", "official_person_ids", "status"], collision_rows)

    # A compatibility cache is rebuildable from raw official cards.  It is not
    # a replacement person master and never overwrites raw R4 runner displays.
    norm = sqlite3.connect(normalized_delta)
    norm.execute("PRAGMA foreign_keys=ON")
    norm.execute("BEGIN IMMEDIATE")
    try:
        norm.execute("""CREATE TABLE IF NOT EXISTS v1_person_category_context(
            race_key TEXT NOT NULL REFERENCES races(race_key), horse_number INTEGER NOT NULL,
            card_capture_id TEXT NOT NULL, card_raw_path TEXT NOT NULL,
            jockey_raw_display TEXT, jockey_official_id TEXT NOT NULL, jockey_registered_name TEXT NOT NULL, jockey_v1_token TEXT NOT NULL,
            trainer_raw_display TEXT, trainer_official_id TEXT NOT NULL, trainer_registered_name TEXT NOT NULL, trainer_v1_token TEXT NOT NULL,
            PRIMARY KEY(race_key,horse_number))""")
        norm.execute("DELETE FROM v1_person_category_context")
        norm.executemany("""INSERT INTO v1_person_category_context VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", [
            (r["race_key"], r["horse_number"], r["card_capture_id"], r["card_raw_path"],
             r["jockey_raw_display"], r["jockey_official_id"], r["jockey_registered_name"], r["jockey_v1_token"],
             r["trainer_raw_display"], r["trainer_official_id"], r["trainer_registered_name"], r["trainer_v1_token"])
            for r in contexts
        ])
        count = norm.execute("SELECT COUNT(*) FROM v1_person_category_context").fetchone()[0]
        fk = norm.execute("PRAGMA foreign_key_check").fetchall()
        if count != len(runner_rows) or fk:
            raise RuntimeError(f"P7_NORMALIZED_CONTEXT_INTEGRITY:{count}:{len(fk)}")
        norm.commit()
    except Exception:
        norm.rollback()
        raise
    finally:
        norm.close()
    summary = {
        "status": "P7_V1_PERSON_CATEGORY_TEXT_SEMANTICS_RECOVERED",
        "source_races": len({row["race_key"] for row in runner_rows}), "source_runners": len(runner_rows),
        "jockey_person_ids": len(approved["jockey"]), "trainer_person_ids": len(approved["trainer"]),
        "unresolved": 0, "raw_displays_preserved": len(contexts),
        "legacy_category_collisions": len(collision_rows),
        "model_seen_tokens": sum(row["model_category_status"] == "SEEN" for row in model_rows),
        "model_unseen_tokens": sum(row["model_category_status"] != "SEEN" for row in model_rows),
        "result_db_accessed": 0,
    }
    (AUDIT / "P7_V1_PERSON_CATEGORY_TEXT_SEMANTICS_RECOVERED.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, sort_keys=True))
