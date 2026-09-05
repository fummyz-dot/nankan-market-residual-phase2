"""R10 bounded official-result status vocabulary audit.

This reads official final-result raw pages only to establish live-history
semantics.  It does not open Market, payout, reconciliation, or model-output
tables, and it never computes performance.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.audit.p2_m07_target_universe import starter_status
from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12b_r10"
MANIFEST = ROOT / "audit" / "data" / "p2_m12b_r9" / "r4_card_manifest.csv"
DELTA = ROOT / "db" / "p2_live_history_delta.sqlite"
HISTORY = ROOT / "db" / "p2_history_context.sqlite"
RAW = ROOT / "data" / "raw" / "live_history_delta" / "official_result_status_audit"
EXPECTED = 204


def write_csv(name: str, rows: list[dict[str, object]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    path = OUT / name; temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def archive(raw: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(raw).hexdigest()
    path = RAW / f"{digest}.html"; path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".tmp"); temporary.write_bytes(raw); temporary.replace(path)
    return digest, str(path.relative_to(ROOT))


def fetch(url: str) -> official.FetchResult:
    last: Exception | None = None
    for _ in range(3):
        try:
            page = official.fetch_race_page(url, 15)
            if 200 <= page.status_code < 300:
                return page
            last = RuntimeError(f"OFFICIAL_HTTP_NON_SUCCESS:{page.status_code}")
        except Exception as exc:
            last = exc
    raise RuntimeError(f"R10_OFFICIAL_RESULT_FETCH_FAILED:{url}:{last}")


def token(row: dict[str, object]) -> str:
    finish = str(row["finish_position_raw"])
    if finish.isdigit():
        return "FINISH_POSITION_NUMERIC"
    if finish and finish != "-":
        return f"FINISH_DISPLAY:{finish}"
    margin = row.get("margin_raw")
    return f"MARGIN_DISPLAY:{margin}" if margin else "FINISH_DISPLAY_EMPTY"


def source_paths() -> dict[str, Path]:
    con = sqlite3.connect(f"file:{DELTA}?mode=ro", uri=True)
    try:
        return {str(url): ROOT / str(path) for url, path in con.execute(
            "SELECT source_url,raw_archive_path FROM source_captures WHERE source_type='OFFICIAL_RESULT'"
        )}
    finally:
        con.close()


def historical_precedent(raw_token: str) -> dict[str, object]:
    con = sqlite3.connect(f"file:{HISTORY}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    try:
        if raw_token == "FINISH_DISPLAY:同着":
            row = con.execute("""SELECT COUNT(*) runners,COUNT(DISTINCT rr.race_key) races,
                SUM(rr.result_status='FINISHED') finished_rows,SUM(rr.finish_position IS NOT NULL) numeric_finish_rows,
                MIN(rr.finish_position) min_finish,MAX(rr.finish_position) max_finish
                FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
                WHERE r.venue_class='NANKAN_TARGET' AND rr.margin_raw='同着'""").fetchone()
            return {"raw_status": raw_token, **dict(row), "normalized_semantic": "STARTER_VALID_FINISH",
                    "historical_representation": "FINISHED + shared positive numeric finish_position + margin_raw=同着"}
        raw_margin = raw_token.removeprefix("MARGIN_DISPLAY:")
        row = con.execute("""SELECT COUNT(*) runners,COUNT(DISTINCT rr.race_key) races,
             GROUP_CONCAT(DISTINCT rr.result_status) result_statuses,
             SUM(rr.finish_position IS NULL) null_finish_rows
             FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
             WHERE r.venue_class='NANKAN_TARGET' AND rr.margin_raw=?""", (raw_margin,)).fetchone()
        return {"raw_status": raw_token, **dict(row), "normalized_semantic": starter_status("RAW_FINISH_STATUS_MISSING", raw_margin, None),
                "historical_representation": "raw missing-finish status governed by frozen M07 registry"}
    finally:
        con.close()


def approved_semantic(raw_token: str) -> str:
    if raw_token == "FINISH_POSITION_NUMERIC":
        return "STARTER_VALID_FINISH"
    if raw_token == "FINISH_DISPLAY:同着":
        return "STARTER_VALID_FINISH"
    if raw_token.startswith("MARGIN_DISPLAY:"):
        value = raw_token.removeprefix("MARGIN_DISPLAY:")
        return starter_status("RAW_FINISH_STATUS_MISSING", value, None)
    return "UNRESOLVED_OUTCOME_STATUS"


def main() -> dict[str, object]:
    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    if len(manifest) != EXPECTED:
        raise RuntimeError(f"R10_MANIFEST_INTEGRITY:{len(manifest)}:{EXPECTED}")
    saved = source_paths(); rows: list[dict[str, object]] = []; fetches = 0
    for ordinal, entry in enumerate(manifest, start=1):
        url = entry["result_url"]
        path = saved.get(url)
        if path is not None and path.exists():
            raw = path.read_bytes(); raw_hash = hashlib.sha256(raw).hexdigest(); raw_path = str(path.relative_to(ROOT)); provenance = "REUSED_COMMITTED_RAW"
            html = official.decode_html(raw)
        else:
            page = fetch(url); raw_hash, raw_path = archive(page.raw); html = official.decode_html(page.raw, page.headers.get("Content-Type")); provenance = "R10_AUDIT_FETCH"; fetches += 1
        identity = official.parse_race_identity(html)
        _resolved, runners = official.parse_history_result_raw_rows(html, identity=identity)
        for runner in runners:
            status_token = token(runner)
            rows.append({"ordinal": ordinal, "race_key": f"P2_RACE_V1::{identity['race_date']}\x1f{identity['venue']}\x1f{identity['race_number']}",
                         "race_date": identity["race_date"], "venue": identity["venue"], "race_number": identity["race_number"],
                         "horse_number": runner["horse_number"], "horse_name": runner["horse_name_exact"],
                         "raw_finish_display": runner["finish_position_raw"], "raw_margin": runner["margin_raw"],
                         "finish_time_raw": runner["finish_time_raw"], "last_3f": runner["last_3f"],
                         "raw_status": status_token, "numeric_finish_present": str(runner["finish_position_raw"]).isdigit(),
                         "starter_evidence": "NUMERIC_FINISH" if str(runner["finish_position_raw"]).isdigit() else "OFFICIAL_RAW_REQUIRES_REGISTRY",
                         "raw_sha256": raw_hash, "raw_path": raw_path, "provenance": provenance})
        if ordinal % 20 == 0:
            print(json.dumps({"phase": "R10_STATUS_VOCABULARY", "races_scanned": ordinal, "total": EXPECTED, "new_fetches": fetches}, ensure_ascii=False), flush=True)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows: grouped[str(row["raw_status"])].append(row)
    vocabulary: list[dict[str, object]] = []; precedents: list[dict[str, object]] = []; unresolved: list[str] = []
    for value, observed in sorted(grouped.items()):
        semantic = approved_semantic(value)
        precedent = historical_precedent(value) if value != "FINISH_POSITION_NUMERIC" else {"raw_status": value, "normalized_semantic": semantic, "historical_representation": "FINISHED + positive numeric finish"}
        if semantic == "UNRESOLVED_OUTCOME_STATUS" or (value != "FINISH_POSITION_NUMERIC" and not int(precedent.get("runners", 0))):
            unresolved.append(value)
        example = observed[0]
        vocabulary.append({"raw_status": value, "runner_count": len(observed), "race_count": len({str(row['race_key']) for row in observed}),
                           "numeric_finish_present_count": sum(bool(row["numeric_finish_present"]) for row in observed),
                           "numeric_finish_missing_count": sum(not bool(row["numeric_finish_present"]) for row in observed),
                           "starter_evidence": "|".join(sorted({str(row["starter_evidence"]) for row in observed})),
                           "example_race": example["race_key"], "historical_semantic_status": precedent.get("normalized_semantic"),
                           "approved_mapping": semantic if value not in unresolved else "BLOCK", "notes": precedent.get("historical_representation")})
        precedents.append(precedent)
    write_csv("official_result_status_raw_rows.csv", rows)
    write_csv("official_result_status_vocabulary.csv", vocabulary)
    write_csv("official_result_status_historical_precedent.csv", precedents)
    ohi = [row for row in rows if row["race_date"] == "2026-08-17" and row["venue"] == "大井" and int(row["race_number"]) == 8 and row["raw_status"] == "FINISH_DISPLAY:同着"]
    write_csv("ohi_20260817_r8_exact_token.csv", ohi)
    result = {"status": "OFFICIAL_RESULT_STATUS_VOCABULARY_RECOVERED" if not unresolved and ohi else "BLOCKED_ON_UNSEEN_OFFICIAL_RESULT_STATUS",
              "races_scanned": len({str(row["race_key"]) for row in rows}), "runner_rows": len(rows), "new_audit_fetches": fetches,
              "observed_tokens": sorted(grouped), "approved": len(grouped) - len(unresolved), "unresolved": sorted(unresolved),
              "current_token": "同着", "performance_evaluated": False, "generated_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "run_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if unresolved or not ohi:
        raise RuntimeError(result["status"] + ":" + "|".join(unresolved))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
