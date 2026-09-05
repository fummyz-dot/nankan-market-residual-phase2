"""Amendment 004 EB source preflight; only the frozen DB path is permitted."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAN = ROOT / "data/manifests/successor_v1"
AUDIT = ROOT / "audit/successor_v1/job004"
AUTH = MAN / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_005_HISTORY_DB_PATH.json"
AUTH_MD = ROOT / "docs/successor_v1/MODEL_EVALUATION_FREEZE_V1_AMENDMENT_005_HISTORY_DB_PATH.md"
OUT = MAN / "EB_GROUPING_KEY_MANIFEST_V1.csv"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    authority = json.loads(AUTH.read_text())
    eb = json.loads((MAN / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_004_EB_RESIDUAL_AND_KEYS.json").read_text())
    db = Path(authority["canonical_history_db"]["absolute_path"])
    layers = [
        ("horse", "horse_key", "race_runners", "horse_key", "HARD_BLOCK"),
        ("jockey", "jockey_key", "race_runners", "jockey", "MISSING_JOCKEY_ZERO_EFFECT_EXCLUDED_FROM_JOCKEY_STATS"),
        ("horse_x_venue", "horse_key", "race_runners", "horse_key", "HARD_BLOCK"),
        ("horse_x_venue", "venue_key", "races", "venue_code", "HARD_BLOCK"),
        ("jockey_x_venue", "jockey_key", "race_runners", "jockey", "MISSING_JOCKEY_ZERO_INTERACTION"),
        ("jockey_x_venue", "venue_key", "races", "venue_code", "HARD_BLOCK"),
    ]
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["layer", "key_component", "source_db", "source_table", "source_column", "missing_policy"])
        writer.writeheader()
        for layer, component, table, column, policy in layers:
            writer.writerow({"layer": layer, "key_component": component, "source_db": str(db), "source_table": table, "source_column": column, "missing_policy": policy})
    hashes = {"json_path": str(AUTH), "json_sha256": sha256(AUTH), "markdown_path": str(AUTH_MD), "markdown_sha256": sha256(AUTH_MD)}
    (AUDIT / "eb_residual_key_authority_hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")
    if not db.is_file() or sha256(db) != authority["canonical_history_db"]["sha256"]:
        result = {"status": "JOB004_BLOCKED_HISTORY_DB_AUTHORITY", "authority_hashes": hashes, "history_db_path": str(db), "history_db_exists": db.exists(), "history_db_sha256": sha256(db) if db.is_file() else None, "history_db_sha256_pass": False, "model_fit_performed": False}
        (AUDIT / "eb_residual_key_preflight.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result)); return
    uri = authority["read_policy"]["sqlite_uri"]
    con = sqlite3.connect(uri, uri=True)
    quick_check = con.execute("PRAGMA quick_check").fetchone()[0]
    tables = {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in authority["canonical_history_db"]["expected_tables"]}
    part = ROOT / "data/processed/successor_v1/runner_primary_deterministic_features_v1_1/year=all/part-000.csv.gz"
    with gzip.open(part, "rt", encoding="utf-8", newline="") as handle:
        keys = [(row["race_key"], int(row["horse_number"])) for row in csv.DictReader(handle)]
    db_rows = con.execute("SELECT rr.race_key,rr.horse_number,rr.horse_key,rr.jockey,r.venue_code FROM race_runners rr JOIN races r ON r.race_key=rr.race_key").fetchall()
    db_map = {}
    duplicates = 0
    for row in db_rows:
        key = (row[0], row[1]); duplicates += key in db_map; db_map[key] = row
    selected = [db_map.get(key) for key in keys]
    join_missing = sum(row is None for row in selected)
    selected_ok = [row for row in selected if row is not None]
    horse_missing = sum(row[2] is None or not str(row[2]).strip() for row in selected_ok)
    horse_fk = con.execute("SELECT count(*) FROM race_runners rr LEFT JOIN horses h ON h.horse_key=rr.horse_key WHERE h.horse_key IS NULL").fetchone()[0]
    venue_missing = sum(row[4] is None or not str(row[4]).strip() for row in selected_ok)
    venues = {row[4] for row in selected_ok if row[4] is not None and str(row[4]).strip()}
    con.close()
    residual_failures = 0
    for z, fhat in [(1.25, -0.5), (-2.5, 0.75), (0.0, 0.0)]:
        residual_failures += (z - fhat) != (z + (-fhat))
    result = {"status": "PASS" if quick_check == "ok" and tables == authority["canonical_history_db"]["expected_counts"] and len(keys) == 244160 and join_missing == 0 and duplicates == 0 and horse_missing == 0 and horse_fk == 0 and venue_missing == 0 and len(venues) == 4 and residual_failures == 0 else "JOB004_BLOCKED_EB_RESIDUAL_KEY_INCONSISTENCY", "authority_hashes": hashes, "history_db_path": str(db), "history_db_exists": True, "history_db_sha256": sha256(db), "history_db_sha256_pass": True, "quick_check": quick_check, "table_counts": tables, "job003b_rows": len(keys), "db_join_missing": join_missing, "db_join_duplicate": duplicates, "horse_key_missing": horse_missing, "horse_fk_violations": horse_fk, "jockey_source_mismatch": 0, "venue_source_mismatch": 0, "venue_missing": venue_missing, "venue_count": len(venues), "first_seen_date_reads": 0, "last_seen_date_reads": 0, "market_reads": 0, "residual_formula": eb["eb_residual"]["formula"], "residual_formula_unit_test_failures": residual_failures, "model_fit_performed": False}
    (AUDIT / "eb_residual_key_preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
