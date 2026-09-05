"""P2-M12B-R7 exact static-official-card pedigree crosswalk audit."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.operations.official_pedigree_identity import PedigreeIdentityError, exact_pedigree_crosswalk


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12b_r7"
RAW = ROOT / "data" / "raw" / "official_pedigree_crosswalk"
DB = ROOT / "db" / "p2_history_context.sqlite"
URLS = [f"https://www.nankankeiba.com/uma_shosai/20260807180501{race:02d}.do" for race in range(1, 13)]


def write_csv(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (OUT / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def archive(result: official.FetchResult, category: str) -> tuple[str, str]:
    digest = hashlib.sha256(result.raw).hexdigest()
    path = RAW / category / f"{digest}.html"; path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".tmp"); temporary.write_bytes(result.raw); temporary.replace(path)
    return digest, str(path.relative_to(ROOT))


def master_profile() -> dict:
    con = sqlite3.connect(DB)
    all_horses, complete = con.execute("SELECT COUNT(*),SUM(CASE WHEN sire IS NOT NULL AND dam IS NOT NULL AND damsire IS NOT NULL THEN 1 ELSE 0 END) FROM horses").fetchone()
    collision_groups = con.execute("""SELECT COUNT(*) FROM (SELECT COUNT(*) FROM horses
        WHERE sire IS NOT NULL AND dam IS NOT NULL AND damsire IS NOT NULL
        GROUP BY horse_name_exact,sire,dam,damsire HAVING COUNT(*)>1)""").fetchone()[0]
    con.close()
    return {"canonical_horses": all_horses, "complete_tuple_horses": complete,
            "missing_tuple_horses": all_horses - complete, "one_to_many_tuple_groups": collision_groups}


def main() -> dict:
    profile = master_profile(); inventory = []; simulation_inputs = []
    for url in URLS:
        result = official.fetch_race_page(url, 15)
        if not 200 <= result.status_code < 300:
            raise RuntimeError("BLOCKED_ON_NONSTARTER_OFFICIAL_IDENTITY:OFFICIAL_CARD_HTTP")
        digest, raw_path = archive(result, "official_card")
        html = official.decode_html(result.raw, result.headers.get("Content-Type")); identity = official.resolve_race(result.final_url, html)
        rows = official.parse_official_pedigree_identity_card(html, identity=identity)
        for row in rows:
            try:
                resolved = exact_pedigree_crosswalk({**row, "official_horse_id": None})
                status = "UNIQUE_RESOLVED"
            except PedigreeIdentityError as error:
                resolved, status = {}, str(error)
            inventory.append({"race_key": f"{identity['race_date']}|{identity['venue']}|{identity['race_number']}",
                              "horse_number": row["horse_number"], **{field: row[field] for field in ("horse_name_exact", "sire", "dam", "damsire")},
                              "official_horse_id_present": bool(row["official_horse_id"]), "source_url": result.final_url,
                              "captured_at": result.captured_at, "raw_archive_path": raw_path, "response_sha256": digest,
                              "status": status, "resolved_horse_identity_key": resolved.get("horse_identity_key"),
                              "resolved_birth_date": resolved.get("birth_date")})
            # The historical hidden-ID simulation measures exact recovery only
            # on tuples that meet the fallback's precondition (one canonical
            # candidate).  Missing-master tuples remain explicitly reported in
            # the inventory and retain the normal direct-ID route.
            if row["official_horse_id"] and status == "UNIQUE_RESOLVED":
                simulation_inputs.append((row, resolved, result.final_url))
    write_csv("current_card_pedigree_inventory.csv", inventory)
    write_csv("canonical_master_uniqueness_audit.csv", [profile])
    resolved_rows = [row for row in inventory if row["status"] == "UNIQUE_RESOLVED"]
    simulation = []
    for row, crosswalk, card_url in simulation_inputs[:100]:
        detail_url = str(row["official_horse_url"])
        detail_result = official.fetch_race_page(detail_url, 15)
        if not 200 <= detail_result.status_code < 300:
            raise RuntimeError("BLOCKED_ON_NONSTARTER_OFFICIAL_IDENTITY:OFFICIAL_DETAIL_HTTP")
        detail_digest, detail_path = archive(detail_result, "official_detail_simulation")
        detail = official.parse_official_horse_detail(official.decode_html(detail_result.raw, detail_result.headers.get("Content-Type")), official_horse_id=str(row["official_horse_id"]))
        if detail["horse_detail_name_identity"] != row["horse_name_exact"]:
            raise RuntimeError("BLOCKED_ON_NONSTARTER_OFFICIAL_IDENTITY:OFFICIAL_CARD_DETAIL_NAME_CONFLICT")
        con = sqlite3.connect(DB)
        expected_rows = con.execute("SELECT horse_identity_key FROM horses WHERE horse_name_exact=? AND birth_date=?", (row["horse_name_exact"], detail["birth_date"])).fetchall()
        con.close()
        expected = expected_rows[0][0] if len(expected_rows) == 1 else None
        actual = crosswalk.get("horse_identity_key") if crosswalk else None
        simulation.append({"horse_number": row["horse_number"], "horse_name_exact": row["horse_name_exact"],
                           "expected_direct_identity": expected, "pedigree_crosswalk_identity": actual,
                           "match": expected == actual, "detail_source_url": detail_result.final_url,
                           "detail_raw_archive_path": detail_path, "detail_response_sha256": detail_digest})
    wrong = [row for row in simulation if not row["match"]]
    blocked = next((row for row in inventory if row["race_key"] == "2026-08-07|浦和|2" and row["horse_number"] == 5), None)
    if blocked is None or blocked["status"] != "UNIQUE_RESOLVED":
        raise RuntimeError("BLOCKED_ON_NONSTARTER_OFFICIAL_IDENTITY:NO_CANONICAL_MATCH")
    write_csv("historical_hidden_detail_id_simulation.csv", simulation)
    write_csv("historical_hidden_detail_id_simulation_summary.csv", [{"tested": len(simulation), "unique_resolved": len(resolved_rows), "wrong_or_unresolved": len(wrong), "status": "PASS" if not wrong and len(simulation) >= 100 else "FAIL"}])
    write_csv("nonstarter_pedigree_crosswalk_audit.csv", [{"race_date": "2026-08-07", "venue": "浦和", "race_number": 2,
        "horse_number": 5, "total_tested": 1, "unique_resolved": 1, "unresolved_missing_fields": 0, "collision": 0,
        "horse_name_exact": blocked["horse_name_exact"], "resolved_birth_date": blocked["resolved_birth_date"],
        "resolved_horse_identity_key": blocked["resolved_horse_identity_key"], "identity_method": "EXACT_OFFICIAL_PEDIGREE_CROSSWALK"}])
    status = "NONSTARTER_OFFICIAL_IDENTITY_RECOVERED" if not wrong and len(simulation) >= 100 else "BLOCKED_ON_NONSTARTER_OFFICIAL_IDENTITY"
    manifest = {"status": status, "master_profile": profile, "simulation_tested": len(simulation), "simulation_wrong": len(wrong),
                "blocked_urawa_r2": {"horse_number": 5, "identity": blocked["resolved_horse_identity_key"], "birth_date": blocked["resolved_birth_date"]},
                "result_source_used": False, "keibabook_used": False, "performance_accessed": False,
                "created_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return manifest


if __name__ == "__main__":
    main()
