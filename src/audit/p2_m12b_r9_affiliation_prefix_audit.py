"""R9 exact official-card affiliation-prefix vocabulary audit.

This is an official-source identity-semantic audit.  It fetches only the
already bounded R4 date range's card/detail pages; it never reads results for
model evaluation, Market data, payout, or performance metrics.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.operations import live_history_update as history


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12b_r9"
RAW = ROOT / "data" / "raw" / "live_history_delta" / "official_card_affiliation_audit"
MASTER = ROOT / "db" / "p2_history_context.sqlite"
START, THROUGH, EXPECTED_RACES = "2026-08-01", "2026-08-20", 204


def write_csv(name: str, rows: list[dict[str, object]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    path = OUT / name
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    temp.replace(path)


def archive(kind: str, raw: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(raw).hexdigest()
    path = RAW / kind / f"{digest}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_suffix(".tmp"); temp.write_bytes(raw); temp.replace(path)
    return digest, str(path.relative_to(ROOT))


def fetch(url: str) -> official.FetchResult:
    last: Exception | None = None
    for _ in range(3):
        try:
            page = official.fetch_race_page(url, 15)
            if 200 <= page.status_code < 300:
                return page
            last = RuntimeError(f"OFFICIAL_HTTP_NON_SUCCESS:{page.status_code}")
        except Exception as exc:  # network boundaries are recorded as a block
            last = exc
    raise RuntimeError(f"R9_OFFICIAL_CARD_FETCH_FAILED:{url}:{last}")


def master_count(name: str, birth_date: str) -> int:
    con = sqlite3.connect(f"file:{MASTER}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT COUNT(*) FROM horses WHERE horse_name_exact=? AND birth_date=?", (name, birth_date)).fetchone()[0])
    finally:
        con.close()


def _finalize(records: list[dict[str, object]], *, manifest_races: int) -> dict[str, object]:
    approved = official.approved_affiliation_prefixes()
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in records:
        grouped[str(row["card_affiliation_prefix"])].append(row)
    vocabulary = []
    errors: list[str] = []
    for prefix, rows in sorted(grouped.items()):
        affiliations = sorted({str(row["trainer_affiliation"]) for row in rows})
        configured = list(approved.get(prefix, ()))
        exact_count = sum(str(row["card_detail_exact"]).lower() == "true" for row in rows)
        collision_count = sum(int(row["canonical_master_match_count"]) > 1 for row in rows)
        status = "PASS"
        if prefix not in approved:
            status = "BLOCK_SOURCE_AFFILIATION_PREFIX_UNRESOLVED"; errors.append(f"{status}:{prefix}")
        elif any(value not in configured for value in affiliations):
            status = "BLOCK_SOURCE_AFFILIATION_PREFIX_CONTEXT_UNRESOLVED"; errors.append(f"{status}:{prefix}")
        elif exact_count != len(rows) or collision_count:
            status = "BLOCK_SOURCE_AFFILIATION_PREFIX_IDENTITY_UNRESOLVED"; errors.append(f"{status}:{prefix}")
        example = rows[0]
        vocabulary.append({"prefix": prefix, "count": len(rows), "race_count": len({str(row["race_key"]) for row in rows}),
                           "example_horse": example["card_horse_name_raw"], "example_race_type": example["race_type_raw"],
                           "trainer_affiliations": "|".join(affiliations), "configured_affiliations": "|".join(configured),
                           "detail_exact_count": exact_count, "collision_count": collision_count, "status": status})
    write_csv("official_card_affiliation_prefix_audit.csv", records)
    write_csv("official_card_affiliation_prefix_vocabulary.csv", vocabulary)
    result = {"status": "OFFICIAL_RUNNER_AFFILIATION_PREFIX_SEMANTICS_RECOVERED" if not errors else errors[0],
              "manifest_races": manifest_races, "observed_prefixes": sorted(grouped), "prefix_rows": len(records),
              "unresolved_prefixes": len(errors), "wrong_identity": sum(str(row["card_detail_exact"]).lower() != "true" for row in records),
              "collisions": sum(int(row["canonical_master_match_count"]) > 1 for row in records),
              "performance_accessed": False, "generated_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "run_manifest.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise RuntimeError(errors[0])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def finalize_from_parts() -> dict[str, object]:
    parts = sorted(OUT.glob("prefix_records_*.csv"))
    progress = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(OUT.glob("prefix_progress_*.json"))]
    covered = {index for row in progress for index in range(int(row["start_index"]), int(row["end_index"]) + 1)}
    if covered != set(range(1, EXPECTED_RACES + 1)):
        raise RuntimeError(f"R9_CARD_AUDIT_INCOMPLETE:{len(covered)}:{EXPECTED_RACES}")
    records: list[dict[str, object]] = []
    for path in parts:
        with path.open(encoding="utf-8", newline="") as handle:
            records.extend(csv.DictReader(handle))
    return _finalize(records, manifest_races=EXPECTED_RACES)


def main(start_index: int = 1, end_index: int = EXPECTED_RACES) -> dict[str, object]:
    races = history.discover(START, THROUGH)
    if len(races) != EXPECTED_RACES:
        raise RuntimeError(f"R9_MANIFEST_INTEGRITY:{len(races)}:{EXPECTED_RACES}")
    write_csv("r4_manifest_integrity.csv", [
        {"start": START, "through": THROUGH, "expected_races": EXPECTED_RACES,
         "observed_races": len(races), "status": "PASS"}
    ])
    write_csv("r4_card_manifest.csv", [
        {"ordinal": index, "card_url": card_url, "result_url": result_url}
        for index, (card_url, result_url) in enumerate(races, start=1)
    ])
    if not (1 <= start_index <= end_index <= len(races)):
        raise ValueError("R9_INVALID_CARD_AUDIT_RANGE")
    approved = official.approved_affiliation_prefixes()
    records: list[dict[str, object]] = []
    details: dict[str, dict[str, str]] = {}
    for ordinal, (card_url, _result_url) in enumerate(races[start_index - 1:end_index], start=start_index):
        try:
            card = fetch(card_url)
        except Exception as exc:
            (OUT / "failure.json").write_text(json.dumps({"ordinal": ordinal, "card_url": card_url, "error": repr(exc)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise
        card_hash, card_path = archive("card", card.raw)
        html = official.decode_html(card.raw, card.headers.get("Content-Type"))
        identity = official.resolve_race(card.final_url, html)
        card_rows = official.parse_official_pedigree_identity_card(
            html, identity=identity, enforce_affiliation_allowlist=False
        )
        context = official.parse_official_card_affiliation_context(html)
        for row in card_rows:
            prefix = row["card_affiliation_prefix"]
            if prefix is None:
                continue
            number = int(row["horse_number"])
            observed_context = context.get(number)
            if observed_context is None or observed_context["horse_name_raw"] != row["card_horse_name_raw"]:
                raise RuntimeError(f"BLOCK_SOURCE_AFFILIATION_PREFIX_CONTEXT_UNRESOLVED:{prefix}:{identity['race_date']}:{number}")
            url = row["official_horse_url"]
            if url is None:
                raise RuntimeError(f"BLOCK_SOURCE_AFFILIATION_PREFIX_DETAIL_UNAVAILABLE:{prefix}:{identity['race_date']}:{number}")
            if url not in details:
                page = fetch(str(url)); detail_hash, detail_path = archive("horse_detail", page.raw)
                parsed = official.parse_official_horse_detail(
                    official.decode_html(page.raw, page.headers.get("Content-Type")), official_horse_id=str(row["official_horse_id"])
                )
                details[str(url)] = parsed | {"detail_raw_sha256": detail_hash, "detail_raw_path": detail_path}
            detail = details[str(url)]
            exact = row["card_horse_name_identity"] == detail["horse_detail_name_identity"]
            records.append({
                "race_key": f"P2_RACE_V1::{identity['race_date']}\x1f{identity['venue']}\x1f{identity['race_number']}",
                "race_date": identity["race_date"], "venue": identity["venue"], "race_number": identity["race_number"],
                "race_type_raw": identity["race_name"], "horse_number": number,
                "card_horse_name_raw": row["card_horse_name_raw"], "card_affiliation_prefix": prefix,
                "card_horse_name_identity": row["card_horse_name_identity"],
                "trainer_affiliation": observed_context["trainer_affiliation"],
                "official_horse_id": row["official_horse_id"],
                "detail_horse_name_raw": detail["horse_detail_name_raw"],
                "detail_horse_name_identity": detail["horse_detail_name_identity"],
                "birth_date": detail["birth_date"], "card_detail_exact": exact,
                "canonical_master_match_count": master_count(str(row["card_horse_name_identity"]), str(detail["birth_date"])),
                "card_raw_sha256": card_hash, "card_raw_path": card_path,
                "detail_raw_sha256": detail["detail_raw_sha256"], "detail_raw_path": detail["detail_raw_path"],
            })
        if (ordinal - start_index + 1) % 20 == 0:
            print(json.dumps({"phase": "CARD_PREFIX_AUDIT", "cards_processed": ordinal, "cards_total": len(races)}, ensure_ascii=False), flush=True)
    write_csv(f"prefix_records_{start_index:03d}_{end_index:03d}.csv", records)
    progress = {"start_index": start_index, "end_index": end_index, "cards_processed": end_index - start_index + 1,
                "status": "PASS", "generated_at": datetime.now(timezone.utc).isoformat()}
    (OUT / f"prefix_progress_{start_index:03d}_{end_index:03d}.json").write_text(json.dumps(progress, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(progress, ensure_ascii=False, sort_keys=True))
    return progress


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--end-index", type=int, default=EXPECTED_RACES)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    finalize_from_parts() if args.finalize else main(args.start_index, args.end_index)
