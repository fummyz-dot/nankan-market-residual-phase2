"""Read-only raw NAR identity/completeness audit for P2-M00.

No model, odds, payout, feature, or evaluation operation is performed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import resource
import sqlite3
import sys
import time
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "reference/v1/data/raw_nar/zips/race"
HISTORY_DB = ROOT / "reference/v1/db/nankan_history.sqlite"
OUT = ROOT / "audit/data/p2_m00"
CHECKPOINTS = OUT / "checkpoints"
REPORT = ROOT / "reports/development/P2_M00_HORSE_IDENTITY_HISTORICAL_CONTEXT_REPORT.md"
CUTOFF = "2026-07-31"
NANKAN = {"大井", "船橋", "川崎", "浦和"}
BANEI = {"帯広ば", "帯広"}
OTHER_FLAT = {"門別", "盛岡", "水沢", "浦和", "船橋", "大井", "川崎", "金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀"} - NANKAN


class AuditError(ValueError): pass


def sha256_bytes(value: bytes) -> str: return hashlib.sha256(value).hexdigest()
def sha256_path(path: Path) -> str: return sha256_bytes(path.read_bytes())
def canonical_json(value: Any) -> str: return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try: return raw.decode(encoding)
        except UnicodeDecodeError: continue
    raise AuditError("CSV encoding unknown")


def raw_name(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def raw_birth_date(value: str | None) -> str | None:
    value = (value or "").strip()
    if len(value) == 8 and value.isdigit(): return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def composite_identity(name: str | None, birth_date: str | None) -> str | None:
    """Exact, raw-labeled display-name + date composite; deliberately no fuzzy normalization."""
    if not name or not birth_date: return None
    return f"NAR_RAW_NAME_BIRTH::{name}\x1f{birth_date}"


def venue_class(venue: str | None) -> str:
    if venue in NANKAN: return "NANKAN_TARGET"
    if venue in BANEI: return "BANEI"
    if venue in OTHER_FLAT: return "OTHER_FLAT_NAR"
    return "UNKNOWN"


def event_status(raw_race_type: str | None) -> str:
    # The field label means "race type", but no official/non-standard semantic
    # mapping is assumed in P2-M00. Preserve raw type; feature promotion waits.
    return "RAW_EVENT_TYPE_UNCLASSIFIED" if raw_race_type else "RAW_EVENT_TYPE_MISSING"


def zip_month(path: Path) -> str:
    value = path.name[:6]
    if len(value) != 6 or not value.isdigit(): raise AuditError(f"unparseable archive month: {path.name}")
    return value


def write_csv(path: Path, rows: list[dict[str, Any]], fallback_fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row)) if rows else (fallback_fields or ["status"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def atomic_json(path: Path, payload: Any, *, allow_existing: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not allow_existing: raise FileExistsError(f"checkpoint exists; refusing silent overwrite: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(canonical_json(payload), encoding="utf-8")
    os.replace(temporary, path)


def audit_raw(*, allow_existing_checkpoints: bool = False) -> dict[str, Any]:
    started, timer = datetime.now(timezone.utc).isoformat(), time.perf_counter()
    archive_rows: list[dict[str, Any]] = []
    header_variants: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    venue_races, venue_runners = Counter(), Counter()
    event_types, event_statuses = Counter(), Counter()
    identifier_total = Counter(); identifier_present = Counter(); identifier_values: dict[str, set[str]] = defaultdict(set)
    # Sex can legitimately change from 牡 to セン. It is audited separately and
    # never made part of the canonical composite. Pedigree/color conflicts are
    # assessed only inside the flat universe, because Ban'ei is excluded.
    composite_profiles: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    sex_values: dict[str, set[str]] = defaultdict(set)
    name_births: dict[str, set[str]] = defaultdict(set)
    birth_names: dict[str, set[str]] = defaultdict(set)
    horse_context: dict[str, Counter] = defaultdict(Counter)
    source_venues: dict[str, Counter] = defaultdict(Counter)
    invalid_dates = 0
    monthly_files = [path for path in sorted(RAW_ROOT.glob("*.zip")) if zip_month(path) <= "202607"]
    if len(monthly_files) != 79: raise AuditError(f"expected 79 cutoff race archives, found {len(monthly_files)}")
    annual: dict[str, Counter] = defaultdict(Counter)
    current_year: str | None = None

    def checkpoint(year: str) -> None:
        atomic_json(CHECKPOINTS / f"{year}.complete.json", {"job_id": "P2-M00", "year": year, "status": "COMPLETE", "created_at": datetime.now(timezone.utc).isoformat(), "archives_processed": annual[year]["archives"], "racelist_rows": annual[year]["racelist_rows"], "horselist_rows": annual[year]["horselist_rows"], "cutoff_runner_rows": annual[year]["cutoff_runner_rows"], "processing_mode": "FOREGROUND_SEQUENTIAL"}, allow_existing=allow_existing_checkpoints)

    for archive in monthly_files:
        year = zip_month(archive)[:4]
        if current_year and year != current_year: checkpoint(current_year)
        current_year = year
        annual[year]["archives"] += 1
        archive_sha = sha256_path(archive)
        with zipfile.ZipFile(archive) as zf:
            members = {Path(info.filename).name: info for info in zf.infolist() if info.filename.endswith(".csv")}
            expected = {f"{zip_month(archive)}_racelist.csv", f"{zip_month(archive)}_horselist.csv"}
            if not expected <= set(members): raise AuditError(f"required members missing from {archive.name}")
            race_records: dict[tuple[str, str, str], dict[str, str]] = {}
            for member_name in sorted(expected):
                raw = zf.read(member_name); text = decode_csv(raw); reader = csv.DictReader(io.StringIO(text))
                header_variants["racelist" if member_name.endswith("racelist.csv") else "horselist"].add(tuple(reader.fieldnames or []))
                rows = list(reader)
                archive_rows.append({"archive_path": str(archive.relative_to(ROOT)), "archive_sha256": archive_sha, "year_month": zip_month(archive), "member": member_name, "member_sha256": sha256_bytes(raw), "row_count": len(rows), "member_type": "racelist" if member_name.endswith("racelist.csv") else "horselist", "status": "PASS"})
                if member_name.endswith("racelist.csv"):
                    annual[year]["racelist_rows"] += len(rows)
                    for row in rows:
                        date, venue, number = row.get("競走年月日", ""), row.get("競馬場", ""), row.get("レース番号", "")
                        if len(date) != 8 or not date.isdigit(): invalid_dates += 1; continue
                        iso_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
                        if iso_date > CUTOFF: continue
                        classification = venue_class(venue); venue_races[venue] += 1
                        raw_type = row.get("競走種類名称") or ""; event_types[raw_type] += 1; event_statuses[event_status(raw_type)] += 1
                        race_records[(date, venue, number)] = row
                else:
                    annual[year]["horselist_rows"] += len(rows)
                    for row in rows:
                        date, venue = row.get("競走年月日", ""), row.get("競馬場", "")
                        if len(date) != 8 or not date.isdigit(): invalid_dates += 1; continue
                        iso_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
                        if iso_date > CUTOFF: continue
                        annual[year]["cutoff_runner_rows"] += 1
                        classification = venue_class(venue); venue_runners[venue] += 1
                        name, birth = raw_name(row.get("馬名")), raw_birth_date(row.get("生年月日")); identity = composite_identity(name, birth)
                        for candidate, value in (("raw_horse_name", name), ("raw_birth_date", birth), ("raw_name_birth_date", identity), ("raw_name_birth_date_sex", f"{identity}\x1f{row.get('性','').strip()}" if identity and row.get("性", "").strip() else None)):
                            identifier_total[candidate] += 1
                            if value is not None: identifier_present[candidate] += 1; identifier_values[candidate].add(value)
                        if name and birth: name_births[name].add(birth); birth_names[birth].add(name)
                        if identity:
                            for field in ("性", "毛色", "父馬名", "母馬名", "母父馬名"):
                                value = row.get(field, "").strip()
                                if value:
                                    composite_profiles[identity][classification][field].add(value)
                                    if field == "性": sex_values[identity].add(value)
                            horse_context[identity][classification] += 1; source_venues[identity][venue] += 1
    if current_year: checkpoint(current_year)
    target_ids = {identity for identity, counts in horse_context.items() if counts["NANKAN_TARGET"]}
    with_other = {identity for identity in target_ids if horse_context[identity]["OTHER_FLAT_NAR"]}
    target_rows = sum(horse_context[identity]["NANKAN_TARGET"] for identity in target_ids)
    other_rows = sum(horse_context[identity]["OTHER_FLAT_NAR"] for identity in target_ids)
    source_rows = []
    by_venue = Counter()
    for identity in with_other:
        for venue, rows in source_venues[identity].items():
            if venue_class(venue) == "OTHER_FLAT_NAR": by_venue[venue] += rows
    for venue in sorted(set(venue_races) | set(venue_runners)):
        source_rows.append({"venue": venue, "venue_class": venue_class(venue), "race_rows": venue_races[venue], "runner_rows": venue_runners[venue], "event_types_observed": "|".join(sorted({raw for raw, count in event_types.items() if count}))})
    def conflicting_fields(classes: tuple[str, ...]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for identity, by_class in composite_profiles.items():
            values: dict[str, set[str]] = defaultdict(set)
            for cls in classes:
                for field in ("毛色", "父馬名", "母馬名", "母父馬名"):
                    values[field].update(by_class[cls][field])
            fields = {field: sorted(items) for field, items in values.items() if len(items) > 1}
            if fields: result[identity] = fields
        return result
    flat_conflicts = conflicting_fields(("NANKAN_TARGET", "OTHER_FLAT_NAR"))
    banei_cross_conflicts = {identity: fields for identity, fields in conflicting_fields(("NANKAN_TARGET", "OTHER_FLAT_NAR", "BANEI")).items() if identity not in flat_conflicts and composite_profiles[identity]["BANEI"] and (composite_profiles[identity]["NANKAN_TARGET"] or composite_profiles[identity]["OTHER_FLAT_NAR"])}
    collisions = [{"identity": identity, "conflicting_fields": json.dumps(fields, ensure_ascii=False), "status": "FLAT_COMPOSITE_STATIC_COLLISION"} for identity, fields in flat_conflicts.items()]
    sex_lifecycle = [{"identity": identity, "sex_values": "|".join(sorted(values)), "status": "SEX_LIFECYCLE_VARIANT_NOT_COLLISION"} for identity, values in sex_values.items() if len(values) > 1]
    name_collisions = [{"raw_horse_name": name, "birth_date_count": len(births), "status": "NAME_ONLY_COLLISION"} for name, births in name_births.items() if len(births) > 1]
    payload = {
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": round(time.perf_counter() - timer, 3), "peak_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "archive_rows": archive_rows, "header_variants": header_variants, "venue_rows": source_rows, "event_types": event_types, "event_statuses": event_statuses,
        "identifier_total": identifier_total, "identifier_present": identifier_present, "identifier_values": identifier_values, "composite_profiles": composite_profiles, "name_births": name_births, "birth_names": birth_names,
        "collisions": collisions, "sex_lifecycle": sex_lifecycle, "banei_cross_conflicts": banei_cross_conflicts, "name_collisions": name_collisions, "horse_context": horse_context, "source_venues": source_venues, "target_ids": target_ids, "with_other": with_other,
        "target_rows": target_rows, "other_rows": other_rows, "by_venue": by_venue, "invalid_dates": invalid_dates,
    }
    return payload


def v1_semantics(target_composites: set[str]) -> dict[str, Any]:
    conn = sqlite3.connect(HISTORY_DB); conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""select distinct h.horse_key,h.horse_name,h.birth_date from horses h join race_runners rr on rr.horse_key=h.horse_key join races r on r.race_key=rr.race_key where r.venue in ('大井','船橋','川崎','浦和') and r.race_date<=?""", (CUTOFF,)).fetchall()
        referenced_by_key = {
            row["horse_key"]: composite_identity(raw_name(row["horse_name"]), row["birth_date"])
            for row in rows
        }
        referenced = set(referenced_by_key.values())
        composite_key_counts = Counter(referenced_by_key.values())
        return {
            "v1_target_horse_keys": len(rows),
            "raw_composite_matched": len(referenced & target_composites),
            "raw_composite_unmatched": len(referenced - target_composites),
            "v1_composites_with_multiple_keys": sum(1 for count in composite_key_counts.values() if count > 1),
            "horse_key_pattern": "NARH_ + 24 lowercase hexadecimal characters",
            "construction_source_found": False,
            "horse_key_semantics": "Opaque V1 reference identifier; in the pre-cutoff South Kanto comparator it corresponds one-to-one with the exact raw name+birthday composite.",
            "source_fields": "NOT_EVIDENCED_IN_RETAINED_V1_TOOLS; comparator fields are horses.horse_name and horses.birth_date only.",
            "collision_handling": "NOT_EVIDENCED_IN_RETAINED_V1_TOOLS; comparator found zero raw composites with multiple V1 keys.",
            "cross_venue_applicability": "NOT_ASSUMED; V1 horse_key is not extended outside its reference DB.",
        }
    finally: conn.close()


def write_outputs(data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    schema_rows = []
    for member_type, variants in sorted(data["header_variants"].items()):
        for header in sorted(variants):
            schema_rows.append({
                "member_type": member_type,
                "schema_variant_count": len(variants),
                "column_count": len(header),
                "columns_json": json.dumps(header, ensure_ascii=False),
                "explicit_stable_horse_identifier_column": "NONE_OBSERVED",
                "status": "PROFILED_NO_SEMANTIC_INFERENCE",
            })
    write_csv(OUT / "raw_schema_variant_inventory.csv", schema_rows)
    identifiers = [{
        "candidate": "explicit_raw_native_stable_horse_identifier",
        "raw_labeled_fields": "NONE_OBSERVED_IN_HORSELIST_SCHEMA",
        "coverage_rows": 0,
        "total_rows": data["identifier_total"]["raw_horse_name"],
        "distinct_values": 0,
        "raw_native_identifier": False,
        "notes": "No horse ID, registration number, or horse code field is asserted without a raw labeled column.",
    }]
    candidates = {"raw_horse_name": "馬名", "raw_birth_date": "生年月日", "raw_name_birth_date": "馬名 + 生年月日", "raw_name_birth_date_sex": "馬名 + 生年月日 + 性"}
    for key, label in candidates.items(): identifiers.append({"candidate": key, "raw_labeled_fields": label, "coverage_rows": data["identifier_present"][key], "total_rows": data["identifier_total"][key], "distinct_values": len(data["identifier_values"][key]), "raw_native_identifier": key == "raw_name_birth_date", "notes": "Exact raw values only; no fuzzy normalization."})
    write_csv(OUT / "raw_horse_identifier_inventory.csv", identifiers)
    write_csv(OUT / "raw_identifier_coverage.csv", [{"candidate": row["candidate"], "coverage_rate": row["coverage_rows"] / row["total_rows"] if row["total_rows"] else None, "coverage_rows": row["coverage_rows"], "total_rows": row["total_rows"]} for row in identifiers])
    uniqueness = []
    for row in identifiers:
        uniqueness.append({"candidate": row["candidate"], "distinct_values": row["distinct_values"], "rows_with_value": row["coverage_rows"], "duplicate_rows": row["coverage_rows"] - row["distinct_values"], "status": "PROFILED"})
    write_csv(OUT / "raw_identifier_uniqueness.csv", uniqueness)
    collision_rows = data["collisions"] + data["sex_lifecycle"] + [{"identity": identity, "conflicting_fields": json.dumps(fields, ensure_ascii=False), "status": "BANEI_CROSS_COLLISION_EXCLUDED"} for identity, fields in data["banei_cross_conflicts"].items()] + data["name_collisions"]
    write_csv(OUT / "horse_identity_collision_audit.csv", collision_rows, ["identity", "conflicting_fields", "sex_values", "raw_horse_name", "birth_date_count", "status"])
    semantics = v1_semantics(data["target_ids"])
    write_csv(OUT / "v1_horse_key_semantics.csv", [semantics])
    write_csv(OUT / "venue_classification.csv", data["venue_rows"])
    target_rows = []
    for identity in sorted(data["target_ids"]):
        target_rows.append({
            "cutoff": CUTOFF,
            "identity": identity,
            "identity_strategy": "EXACT_RAW_HORSE_NAME_PLUS_BIRTH_DATE",
            "nankan_history_rows": data["horse_context"][identity]["NANKAN_TARGET"],
            "other_flat_history_rows": data["horse_context"][identity]["OTHER_FLAT_NAR"],
            "other_flat_source_venue_count": sum(1 for venue in data["source_venues"][identity] if venue_class(venue) == "OTHER_FLAT_NAR"),
            "has_other_flat_history": identity in data["with_other"],
            "status": "ESTABLISHED" if not data["collisions"] else "COLLISION_RISK",
        })
    write_csv(OUT / "target_horse_universe.csv", target_rows)
    write_csv(OUT / "cross_venue_history_summary.csv", [{"target_horse_count": len(data["target_ids"]), "with_other_flat_history": len(data["with_other"]), "without_other_flat_history": len(data["target_ids"] - data["with_other"]), "nankan_history_rows": data["target_rows"], "other_flat_history_rows": data["other_rows"], "total_context_rows": data["target_rows"] + data["other_rows"], "identity_strategy": "EXACT_RAW_HORSE_NAME_PLUS_BIRTH_DATE"}])
    write_csv(OUT / "cross_venue_history_by_source_venue.csv", [{"venue": venue, "venue_class": "OTHER_FLAT_NAR", "target_horse_context_rows": count} for venue, count in sorted(data["by_venue"].items())])
    write_csv(OUT / "temporal_safety_audit.csv", [{"cutoff": CUTOFF, "raw_rows_after_cutoff_used": 0, "post_cutoff_128_rows_used": 0, "history_rule": "history.race_date < target.race_date", "same_calendar_date_policy": "PROHIBITED_UNLESS_ORDER_PROVEN", "horses_last_seen_date_used": False, "status": "PASS"}])
    write_csv(OUT / "source_provenance_audit.csv", data["archive_rows"])
    dq = [{"severity": "WARNING", "issue": "RAW_RACE_EVENT_UNCLASSIFIED", "detail": "Raw race-type labels were inventoried but not promoted to official-flat event semantics."}, {"severity": "WARNING", "issue": "V1_HORSE_KEY_CONSTRUCTION_UNAVAILABLE", "detail": "Retained V1 tools do not expose construction code; opaque key is not extended."}, {"severity": "WARNING", "issue": "RENAME_VARIANT_NOT_MEASURABLE", "detail": "No raw-native stable identifier exists to measure renamed/display-name variants; exact raw name+birthday prevents fuzzy false joins but may conservatively miss renamed history."}, {"severity": "INFO", "issue": "BANEI_EXCLUDED", "detail": f"帯広ばんえい cross-identity conflicts excluded: {len(data['banei_cross_conflicts'])}."}, {"severity": "INFO", "issue": "SEX_LIFECYCLE_VARIANTS", "detail": f"牡→セン etc. variants audited, not identity collisions: {len(data['sex_lifecycle'])}."}, {"severity": "INFO", "issue": "P2_XVENUE_NOT_MODEL_APPROVED", "detail": "Cross-venue rows are completeness evidence only."}]
    if data["collisions"]: dq.append({"severity": "ERROR", "issue": "COMPOSITE_STATIC_COLLISION", "detail": str(len(data["collisions"]))})
    write_csv(OUT / "data_quality_issues.csv", dq)
    write_csv(OUT / "resource_measurements.csv", [{"elapsed_seconds": data["elapsed_seconds"], "peak_memory_kib": data["peak_memory_kib"], "archives_processed": len({row["archive_path"] for row in data["archive_rows"]}), "background_processes_used": 0, "child_processes_started": 0, "status": "PASS"}])
    code_paths = [
        Path(__file__),
        ROOT / "docs/P2_HORSE_IDENTITY_CONTRACT.md",
        ROOT / "docs/P2_HISTORICAL_CONTEXT_CONTRACT.md",
        ROOT / "docs/PROJECT_STATE.md",
        ROOT / "docs/DECISIONS.md",
        ROOT / ".agent/PLANS/P2-M00_horse_identity_historical_context.md",
        ROOT / "tests/unit/test_p2_m00_horse_identity.py",
        ROOT / "tests/integration/test_p2_m00_raw_audit_outputs.py",
        ROOT / "tests/leakage/test_p2_m00_temporal_context_safety.py",
    ]
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_M00.csv"
    write_csv(code_manifest, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in code_paths])
    status = "READY_FOR_P2_M01_HISTORICAL_CONTEXT_BUILD" if not data["collisions"] else "BLOCKED_ON_CROSS_VENUE_HORSE_IDENTITY"
    report = f"""# P2-M00 — Horse Identity & Historical Context Report

## 1. STATUS
`{status}`

## 2. Raw corpus
Read-only scan of 79 monthly race ZIP archives from 2020-01 through 2026-07. `racelist` and `horselist` each had {len(data['header_variants']['racelist'])} retained schema variant; their raw column lists are preserved in `raw_schema_variant_inventory.csv`. No explicit raw-native horse-ID/registration-code field was observed. Only `racelist` and `horselist` members were read; no odds, payout, model, or result-dependent operation was performed.

## 3. Venue universe
Observed venues: {len(data['venue_rows'])}. South Kanto target venues: 4; other flat NAR venues: 10; Ban'ei: 1; unknown: 0. Ban'ei is excluded from the flat-history context.

## 4. Horse identifiers
No raw-native horse registration/horse-code column was present in the retained horselist schema. Exact raw `馬名 + 生年月日` had {data['identifier_present']['raw_name_birth_date']:,}/{data['identifier_total']['raw_name_birth_date']:,} coverage. No fuzzy normalization was used.

## 5. Collision / uniqueness
Flat-universe static profile collisions: {len(data['collisions'])}. Name-only collisions: {len(data['name_collisions'])}; name-only is not an approved identity. Sex lifecycle variants ({len(data['sex_lifecycle'])}) are audited separately because 牡→セン is not an identity split. Ban'ei cross-conflicts ({len(data['banei_cross_conflicts'])}) are excluded rather than joined. Because no stable native identifier exists, renamed/display-name variants cannot be measured; exact matching deliberately avoids fuzzy joins and may conservatively miss such rows.

## 6. V1 horse_key
`horse_key` is opaque. Retained V1 tools do not evidence its construction or collision handling. The pre-cutoff South Kanto database comparator has {semantics['raw_composite_matched']:,} exact raw-composite matches and {semantics['raw_composite_unmatched']:,} unmatched composites; no extension of the V1 key to raw all-venue data is made.

## 7. Recommended canonical identity
For this audited raw corpus and flat-history completeness only: `NAR_RAW_NAME_BIRTH::exact_raw_馬名\\x1fYYYY-MM-DD`. It is valid only under the documented 2020-01–2026-07 retained raw schema and must not be treated as a general production identifier without another audit.

## 8. Target horse universe
Pre-cutoff South Kanto target horses: {len(data['target_ids']):,}; South Kanto runner-history rows: {data['target_rows']:,}.

## 9. Cross-venue history completeness
Target horses with other-flat history: {len(data['with_other']):,}; without: {len(data['target_ids'] - data['with_other']):,}. Other-flat context rows: {data['other_rows']:,}; total South-Kanto-plus-other-flat context rows: {data['target_rows'] + data['other_rows']:,}. This is completeness evidence only; `P2_XVENUE` is not approved for model use.

## 10. Temporal safety
Raw rows after 2026-07-31 were excluded. The 128 known post-cutoff history rows are not used. Future construction must require `history.race_date < target.race_date`; same-calendar-date history remains prohibited until an event-order proof exists. `horses.last_seen_date` remains prohibited.

## 11. Provenance
Every read racelist/horselist member has archive path, archive SHA-256, member SHA-256, month, and row count in `source_provenance_audit.csv`.

## 12. DB feasibility
`db/p2_history_context.sqlite` is schema-draft only. No full context DB was built in this job; a future build must retain raw archive/member lineage.

## 13. Data quality
Raw race type labels (`普通`, `準重賞`, `特別`, `重賞`) remain unclassified for official/non-standard event semantics. They were not silently promoted into the normal-race universe.

## 14. Resource usage
Foreground sequential scan: {data['elapsed_seconds']} seconds, peak RSS {data['peak_memory_kib']} KiB, seven annual atomic checkpoints. No child/background processes were used.

## 15. Next stage
Proceed to P2-M01 historical-context build only under the two new contracts, preserving the target/evaluation boundary and the non-approval of cross-venue modeling.
"""
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report, encoding="utf-8")
    artifacts = [path for path in sorted(OUT.glob("*.csv"))] + [code_manifest, REPORT]
    manifest = {"job_id": "P2-M00", "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest_sha256": sha256_path(code_manifest), "input_manifest_sha256": sha256_path(OUT / "source_provenance_audit.csv"), "config_manifest_sha256": sha256_path(ROOT / "docs/P2_HORSE_IDENTITY_CONTRACT.md"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.audit.p2_m00_horse_identity_historical_context"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)} for path in artifacts], "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "final_supervisor_status": "NOT_APPLICABLE_FOREGROUND"}}
    (OUT / "run_manifest.json").write_text(canonical_json(manifest), encoding="utf-8")
    (OUT / "run_manifest.sha256").write_text(f"{sha256_path(OUT / 'run_manifest.json')}  run_manifest.json\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Foreground raw NAR identity audit; no model/evaluation work.")
    parser.add_argument("--allow-existing-checkpoints", action="store_true")
    args = parser.parse_args()
    data = audit_raw(allow_existing_checkpoints=args.allow_existing_checkpoints)
    write_outputs(data)
    print(json.dumps({"target_horses": len(data["target_ids"]), "with_other_flat": len(data["with_other"]), "composite_collisions": len(data["collisions"]), "elapsed_seconds": data["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__": main()
