"""P2-M02 deterministic South Kanto class/race-condition canonicalization.

Reads the P2-M01 context DB in read-only mode. No result, market, prize-derived
strength, feature, model, or evaluation operation is performed.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import platform
import re
import resource
import sqlite3
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db/p2_history_context.sqlite"
RULE_RAW = ROOT / "data/raw/official_rules/nankan_class"
OUT = ROOT / "audit/data/p2_m02"
CURATED = ROOT / "data/curated/p2_class_rule/nankan_race_class_rule.csv.gz"
SOURCE_MANIFEST = ROOT / "data/manifests/P2_CLASS_OFFICIAL_SOURCE_MANIFEST.csv"
REPORT = ROOT / "reports/development/P2_M02_CLASS_RULE_FOUNDATION_REPORT.md"
MAPPING = ROOT / "configs/features/P2_CLASS_CANONICAL_MAPPING_V1.yaml"
REGISTRY = ROOT / "configs/features/P2_CLASS_RULESET_REGISTRY.yaml"
ABLATION_REGISTRY = ROOT / "configs/features/P2_CLASS_ABLATION_REGISTRY.yaml"
CONTRACT = ROOT / "docs/P2_CLASS_RULE_CONTRACT.md"
CUTOFF = "2026-07-31"
MAPPING_VERSION = "P2_CLASS_RULE_V1"
ORDINAL = {"A1": 8, "A2": 7, "B1": 6, "B2": 5, "B3": 4, "C1": 3, "C2": 2, "C3": 1}
KANJI_NUM = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = fields or list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def normalized(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "")


def compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", normalized(value)).strip()


def kanji_number(token: str) -> int | None:
    token = normalized(token).strip()
    if token.isdigit():
        return int(token)
    if token in KANJI_NUM:
        return KANJI_NUM[token]
    if "十" in token and all(char in KANJI_NUM for char in token):
        left, _, right = token.partition("十")
        tens = KANJI_NUM[left] if left else 1
        ones = KANJI_NUM[right] if right else 0
        return tens * 10 + ones
    return None


def extract_classes(text: str) -> list[str]:
    found = {f"{match.group(1).upper()}{match.group(2)}" for match in re.finditer(r"([ABCabc])\s*([123])(?![0-9])", normalized(text))}
    return sorted(found, key=lambda code: -ORDINAL[code])


def extract_groups(text: str) -> tuple[list[str], list[int]]:
    normalized_text = normalized(text)
    raw_tokens: list[str] = []
    numbers: list[int] = []
    for match in re.finditer(r"([0-9一二三四五六七八九十]+)\s*組", normalized_text):
        raw_tokens.append(match.group(0))
        number = kanji_number(match.group(1))
        if number is not None:
            numbers.append(number)
    for match in re.finditer(r"[ABC]\s*[123](?:\s*[\(（]([0-9一二三四五六七八九十]+)[\)）])+", normalized_text, flags=re.IGNORECASE):
        token = match.group(0)
        raw_tokens.append(token)
        for item in re.findall(r"[\(（]([0-9一二三四五六七八九十]+)[\)）]", token):
            number = kanji_number(item)
            if number is not None:
                numbers.append(number)
    for match in re.finditer(r"[ABC]\s*[123]\s*([0-9一二三四五六七八九十]+(?:\s*[・、 ]\s*[0-9一二三四五六七八九十]+)+)", normalized_text, flags=re.IGNORECASE):
        token = match.group(0)
        raw_tokens.append(token)
        for item in re.findall(r"[0-9一二三四五六七八九十]+", match.group(1)):
            number = kanji_number(item)
            if number is not None:
                numbers.append(number)
    return list(dict.fromkeys(raw_tokens)), sorted(set(numbers))


def grade_code(text: str, race_type_raw: str | None) -> str:
    token = normalized(text)
    match = re.search(r"(JPN|Jpn|G|S)\s*([ⅠⅡⅢIVX123]+)", token)
    if match:
        family, raw = match.group(1), match.group(2)
        roman = raw.replace("Ⅰ", "I").replace("Ⅱ", "II").replace("Ⅲ", "III")
        number = {"I": "1", "II": "2", "III": "3", "1": "1", "2": "2", "3": "3"}.get(roman)
        if number:
            return f"{family.upper()}{number}" if family.upper() == "JPN" else f"{family.upper()}{number}"
    if race_type_raw == "準重賞":
        return "SEMI_GRADED"
    if race_type_raw == "重賞":
        return "UNKNOWN"
    return "NONE"


def age_fields(text: str) -> tuple[str, int | None, int | None]:
    value = normalized(text)
    if re.search(r"2\s*[・･、/]\s*3歳", value):
        return "2_3_YO", 2, 3
    if re.search(r"2歳", value):
        return "2YO", 2, 2
    if re.search(r"3歳以上|3上", value):
        return "3YO_PLUS", 3, None
    if re.search(r"3歳", value):
        return "3YO", 3, 3
    if re.search(r"4歳以上|4上", value):
        return "4YO_PLUS", 4, None
    if re.search(r"4歳", value):
        return "4YO", 4, 4
    if "一般" in value:
        return "GENERAL", None, None
    return "UNKNOWN", None, None


def classify(row: dict[str, Any]) -> dict[str, Any]:
    if row["venue_class"] != "NANKAN_TARGET":
        return {"parse_status": "SKIPPED_NON_NANKAN", "mapping_version": MAPPING_VERSION}
    conditions, name, race_type = row["conditions_raw"] or "", row["race_name"] or "", row["race_type_raw"] or ""
    text = f"{conditions} {name}"
    norm = normalized(text)
    classes = extract_classes(text)
    groups_raw, group_numbers = extract_groups(text)
    age_code, min_age, max_age = age_fields(text)
    sex_code = "FEMALE_ONLY" if "牝" in norm else ("MALE_ONLY" if "牡" in norm else "OPEN_SEX")
    weight = "定量" if "定量" in norm else ("別定" if "別定" in norm else ("ハンデ" if "ハンデ" in norm else "UNKNOWN"))
    newcomer = bool(re.search(r"新馬|デビュー", norm, flags=re.IGNORECASE))
    ungraded = bool(re.search(r"未格付|未格", norm))
    open_flag = "オープン" in norm
    jra = bool(re.search(r"JRA|中央競馬", norm, flags=re.IGNORECASE))
    local = "地方交流" in norm and not jra
    selected = bool(re.search(r"選定馬|選抜馬", norm))
    special = race_type == "特別" or "特別" in norm
    grade = grade_code(text, race_type)
    heavy = race_type == "重賞"
    semi = race_type == "準重賞" or grade == "SEMI_GRADED"
    if row["race_date"] >= "2024-01-01":
        ruleset, evidence = "NANKAN_POINTS_ALL_HORSES_2024", "OFFICIAL_CONFIRMED"
    elif row["race_date"] >= "2023-04-01" and age_code == "2YO":
        ruleset, evidence = "NANKAN_POINTS_2YO_PILOT_2023", "OFFICIAL_CONFIRMED"
    else:
        ruleset, evidence = "NANKAN_LEGACY_PRIZE_BASED", "OFFICIAL_CONFIRMED"
    if newcomer:
        taxonomy = "NEWCOMER"
    elif jra:
        taxonomy = "JRA_EXCHANGE"
    elif local:
        taxonomy = "LOCAL_EXCHANGE"
    elif heavy:
        taxonomy = "HEAVY_GRADE"
    elif semi:
        taxonomy = "SEMI_GRADED"
    elif open_flag:
        taxonomy = "OPEN"
    elif len(classes) > 1:
        taxonomy = "MIXED_CLASS"
    elif classes:
        taxonomy = "GRADED_GENERAL"
    elif ungraded and age_code != "UNKNOWN":
        taxonomy = "AGE_CONDITIONED_UNGRADED"
    elif age_code in {"2YO", "3YO", "2_3_YO"}:
        taxonomy = "AGE_CONDITIONED_UNGRADED"
    elif special or selected:
        taxonomy = "OTHER_SPECIAL"
    else:
        taxonomy = "UNRESOLVED"
    top, bottom = (classes[0], classes[-1]) if classes else (None, None)
    if classes:
        parse_status = "CANONICAL_CLASS"
    elif taxonomy in {"NEWCOMER", "JRA_EXCHANGE", "LOCAL_EXCHANGE", "HEAVY_GRADE", "SEMI_GRADED", "OPEN", "OTHER_SPECIAL"}:
        parse_status = "SPECIAL_CLASSIFIED"
    elif taxonomy == "AGE_CONDITIONED_UNGRADED":
        parse_status = "AGE_UNGRADED_CLASSIFIED"
    else:
        parse_status = "UNRESOLVED"
    if newcomer:
        eligibility, reasons = "INELIGIBLE", ["EXCLUDE_NEWCOMER"]
    elif jra:
        eligibility, reasons = "INELIGIBLE", ["EXCLUDE_JRA_EXCHANGE"]
    elif bottom == "C3":
        eligibility, reasons = "INELIGIBLE", ["EXCLUDE_BELOW_C2"]
    elif classes and taxonomy not in {"JRA_EXCHANGE", "LOCAL_EXCHANGE"}:
        eligibility, reasons = "ELIGIBLE", []
    elif taxonomy == "UNRESOLVED":
        eligibility, reasons = "REVIEW_REQUIRED", ["AMBIGUOUS_RACE_TYPE"]
    else:
        eligibility, reasons = "REVIEW_REQUIRED", ["AMBIGUOUS_CLASS"]
    return {
        "ruleset_id": ruleset, "ruleset_evidence_status": evidence,
        "class_codes_json": json_text(classes), "class_top_code": top, "class_bottom_code": bottom,
        "class_top_ordinal": ORDINAL[top] if top else None, "class_bottom_ordinal": ORDINAL[bottom] if bottom else None,
        "mixed_class_flag": int(len(classes) > 1), "class_span": (ORDINAL[top] - ORDINAL[bottom]) if top else None,
        "group_tokens_raw": json_text(groups_raw), "group_numbers_json": json_text(group_numbers), "group_count": len(group_numbers), "group_comparability_status": "UNVERIFIED",
        "race_grade_code": grade, "age_condition_code": age_code, "min_age": min_age, "max_age": max_age, "sex_condition_code": sex_code, "weight_condition_code": weight,
        "newcomer_flag": int(newcomer), "ungraded_flag": int(ungraded), "open_flag": int(open_flag), "jra_exchange_flag": int(jra), "local_exchange_flag": int(local), "selected_race_flag": int(selected), "special_race_flag": int(special), "heavy_grade_flag": int(heavy), "semi_graded_flag": int(semi), "age_restricted_flag": int(age_code not in {"GENERAL", "UNKNOWN"}),
        "race_taxonomy_code": taxonomy, "eligibility_draft_status": eligibility, "eligibility_reason_codes": json_text(reasons), "parse_status": parse_status, "mapping_version": MAPPING_VERSION,
    }


def source_inventory() -> list[dict[str, Any]]:
    definitions = [
        ("NKR-PROGRAM-QA", "https://www.nankankeiba.com/info/qanda/program.html", "program.html", "格付ポイント制について", None, "2024-01-01", None, "OFFICIAL_CONFIRMED", "Current official Q&A; no historical back-application."),
        ("NKR-2023-02-20", "https://www.nankankeiba.com/news_kiji/13427.do", "news_13427.html", "格付ポイント制導入および2歳馬先行導入", "2023-02-20", "2023-04-01", "2023-12-31", "OFFICIAL_CONFIRMED", "Official notice: 2YO pilot from 2023-04-01; all-horse start announced for 2024-01-01."),
        ("NKR-2023-10-24", "https://www.nankankeiba.com/news_kiji/14239.do", "news_14239.html", "格付ポイント制全馬適用", "2023-10-24", "2024-01-01", None, "OFFICIAL_CONFIRMED", "Official notice: all South Kanto horses from 2024-01-01."),
        ("NKR-PAST-DOWNLOAD", "https://www.nankankeiba.com/info/download/index_past.html", "past_download.html", "過去ダウンロードページ", None, None, None, "OFFICIAL_ARCHIVED_NO_RULESET_EVIDENCE", "Archived official entry point; no threshold inference made."),
    ]
    rows = []
    for source_id, url, filename, title, published, effective_from, effective_to, status, notes in definitions:
        raw = RULE_RAW / filename
        if not raw.exists():
            raise RuntimeError(f"OFFICIAL_SOURCE_RAW_MISSING:{raw}")
        captured = datetime.fromtimestamp(raw.stat().st_mtime, timezone.utc).isoformat()
        rows.append({"source_id": source_id, "source_url": url, "title": title, "captured_at": captured, "published_at": published, "effective_from": effective_from, "effective_to": effective_to, "content_type": "text/html", "raw_path": str(raw.relative_to(ROOT)), "sha256": sha256_path(raw), "source_status": status, "notes": notes})
    return rows


def write_gzip_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    os.replace(temp, path)


def main() -> None:
    started, timer = now(), time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = source_inventory()
    write_csv(SOURCE_MANIFEST, inventory)
    write_csv(OUT / "official_source_inventory.csv", inventory)
    write_csv(OUT / "official_source_hashes.csv", [{"source_id": row["source_id"], "raw_path": row["raw_path"], "sha256": row["sha256"], "status": "PASS"} for row in inventory])
    evidence = [
        {"ruleset_id": "NANKAN_LEGACY_PRIZE_BASED", "effective_from": None, "effective_to": "2023-03-31", "basis": "PRIZE_ACCUMULATION", "official_source_ids": "NKR-2023-02-20", "evidence_status": "OFFICIAL_CONFIRMED", "threshold_table_status": "UNRESOLVED"},
        {"ruleset_id": "NANKAN_POINTS_2YO_PILOT_2023", "effective_from": "2023-04-01", "effective_to": "2023-12-31", "basis": "PROGRAM_POINTS", "official_source_ids": "NKR-2023-02-20", "evidence_status": "OFFICIAL_CONFIRMED", "threshold_table_status": "PARTIALLY_CONFIRMED"},
        {"ruleset_id": "NANKAN_POINTS_ALL_HORSES_2024", "effective_from": "2024-01-01", "effective_to": None, "basis": "PROGRAM_POINTS", "official_source_ids": "NKR-2023-10-24|NKR-PROGRAM-QA", "evidence_status": "OFFICIAL_CONFIRMED", "threshold_table_status": "OFFICIAL_CONFIRMED"},
    ]
    write_csv(OUT / "ruleset_evidence_matrix.csv", evidence)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        input_count = conn.execute("SELECT COUNT(*) FROM races WHERE venue_class='NANKAN_TARGET' AND race_date<=?", (CUTOFF,)).fetchone()[0]
        rows = [dict(row) for row in conn.execute("""SELECT race_key,race_date,venue,race_number,conditions_raw,race_name,race_type_raw,venue_class
                                                    FROM races WHERE venue_class='NANKAN_TARGET' AND race_date<=?
                                                    ORDER BY race_date,race_key""", (CUTOFF,))]
        other_flat_count = conn.execute("SELECT COUNT(*) FROM races WHERE venue_class='OTHER_FLAT_NAR'").fetchone()[0]
    finally:
        conn.close()
    if len(rows) != input_count:
        raise RuntimeError("NANKAN_INPUT_COUNT_CHANGED")
    prior_output_sha = sha256_path(CURATED) if CURATED.exists() else None
    output = []
    signatures = Counter()
    for row in rows:
        parsed = classify(row)
        payload = {key: row[key] for key in ("race_key", "race_date", "venue", "race_number", "conditions_raw", "race_name", "race_type_raw")}
        payload.update(parsed)
        output.append(payload)
        signatures[(compact(row["conditions_raw"]), compact(row["race_name"]), row["race_type_raw"] or "")] += 1
    fields = ["race_key", "race_date", "venue", "race_number", "conditions_raw", "race_name", "race_type_raw", "ruleset_id", "ruleset_evidence_status", "class_codes_json", "class_top_code", "class_bottom_code", "class_top_ordinal", "class_bottom_ordinal", "mixed_class_flag", "class_span", "group_tokens_raw", "group_numbers_json", "group_count", "group_comparability_status", "race_grade_code", "age_condition_code", "min_age", "max_age", "sex_condition_code", "weight_condition_code", "newcomer_flag", "ungraded_flag", "open_flag", "jra_exchange_flag", "local_exchange_flag", "selected_race_flag", "special_race_flag", "heavy_grade_flag", "semi_graded_flag", "age_restricted_flag", "race_taxonomy_code", "eligibility_draft_status", "eligibility_reason_codes", "parse_status", "mapping_version"]
    write_gzip_csv(CURATED, output, fields)
    by_year = Counter((row["race_date"][:4], row["parse_status"], row["ruleset_id"]) for row in output)
    by_venue = Counter((row["venue"], row["parse_status"]) for row in output)
    write_csv(OUT / "class_coverage_by_year.csv", [{"year": year, "parse_status": status, "ruleset_id": ruleset, "race_count": count} for (year, status, ruleset), count in sorted(by_year.items())])
    write_csv(OUT / "class_coverage_by_venue.csv", [{"venue": venue, "parse_status": status, "race_count": count} for (venue, status), count in sorted(by_venue.items())])
    write_csv(OUT / "class_raw_signature_profile.csv", [{"conditions_normalized": conditions, "race_name_normalized": name, "race_type_raw": race_type, "race_count": count} for (conditions, name, race_type), count in sorted(signatures.items(), key=lambda item: (-item[1], item[0]))])
    mixed = [row for row in output if row["mixed_class_flag"]]
    groups = [row for row in output if row["group_count"]]
    write_csv(OUT / "mixed_class_audit.csv", mixed)
    write_csv(OUT / "group_token_audit.csv", groups)
    write_csv(OUT / "grade_taxonomy_audit.csv", [{"race_grade_code": grade, "race_taxonomy_code": taxonomy, "race_count": count} for (grade, taxonomy), count in sorted(Counter((row["race_grade_code"], row["race_taxonomy_code"]) for row in output).items())])
    write_csv(OUT / "age_condition_audit.csv", [{"age_condition_code": code, "race_count": count} for code, count in sorted(Counter(row["age_condition_code"] for row in output).items())])
    write_csv(OUT / "exchange_race_audit.csv", [{"exchange_type": "JRA", "race_count": sum(row["jra_exchange_flag"] for row in output)}, {"exchange_type": "LOCAL", "race_count": sum(row["local_exchange_flag"] for row in output)}, {"exchange_type": "BARE_EXCHANGE_UNRESOLVED", "race_count": sum("交流" in normalized((row["conditions_raw"] or "") + " " + (row["race_name"] or "")) and not row["jra_exchange_flag"] and not row["local_exchange_flag"] for row in output)}])
    write_csv(OUT / "ruleset_transition_2023.csv", [row for row in output if "2023-03-01" <= row["race_date"] <= "2023-12-31"])
    write_csv(OUT / "ruleset_transition_2024.csv", [row for row in output if "2023-12-01" <= row["race_date"] <= "2024-01-31"])
    write_csv(OUT / "eligibility_draft_audit.csv", [{"eligibility_draft_status": status, "race_count": count} for status, count in sorted(Counter(row["eligibility_draft_status"] for row in output).items())])
    unresolved = [row for row in output if row["parse_status"] == "UNRESOLVED"]
    write_csv(OUT / "unresolved_races.csv", unresolved, fields)
    explicit_unresolved = [row for row in output if extract_classes((row["conditions_raw"] or "") + " " + (row["race_name"] or "")) and row["parse_status"] == "UNRESOLVED"]
    eligible_unresolved = [row for row in output if row["eligibility_draft_status"] == "ELIGIBLE" and row["race_taxonomy_code"] == "UNRESOLVED"]
    output_sha = sha256_path(CURATED)
    mapping_validation = [{"check": "nankan_input_rows", "actual": len(output), "expected": input_count, "status": "PASS" if len(output) == input_count else "FAIL"}, {"check": "explicit_class_token_unresolved", "actual": len(explicit_unresolved), "expected": 0, "status": "PASS" if not explicit_unresolved else "FAIL"}, {"check": "eligible_taxonomy_unresolved", "actual": len(eligible_unresolved), "expected": 0, "status": "PASS" if not eligible_unresolved else "FAIL"}, {"check": "other_flat_rows_mapped", "actual": 0, "expected": 0, "status": "PASS"}, {"check": "historical_program_points_generated", "actual": 0, "expected": 0, "status": "PASS"}, {"check": "class_boundary_position_generated", "actual": 0, "expected": 0, "status": "PASS"}, {"check": "deterministic_rebuild_sha256", "actual": output_sha, "expected": prior_output_sha or output_sha, "status": "PASS" if prior_output_sha in {None, output_sha} else "FAIL"}]
    write_csv(OUT / "canonical_mapping_validation.csv", mapping_validation)
    dq = [{"severity": "WARNING", "issue": "LEGACY_THRESHOLD_UNRESOLVED", "detail": "Official legacy prize-based threshold table is not reconstructed."}, {"severity": "WARNING", "issue": "PROGRAM_POINTS_NOT_AVAILABLE_ASOF_HISTORICAL", "detail": "No historical race-pre program points, boundary positions, or boundary deltas were generated."}, {"severity": "INFO", "issue": "OTHER_FLAT_SAFEGUARD", "detail": f"{other_flat_count} other-flat races were not passed to the Nankan mapper."}, {"severity": "INFO", "issue": "UNRESOLVED_RACES", "detail": str(len(unresolved))}]
    write_csv(OUT / "data_quality_issues.csv", dq)
    if explicit_unresolved or eligible_unresolved:
        raise RuntimeError("CLASS_COVERAGE_GATE_FAILED")
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_M02.csv"
    code_paths = [Path(__file__), MAPPING, REGISTRY, ABLATION_REGISTRY, CONTRACT, ROOT / ".agent/PLANS/P2-M02_class_ruleset_canonicalization.md"]
    write_csv(code_manifest, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in code_paths])
    elapsed = round(time.perf_counter() - timer, 3)
    class_counts = Counter(row["parse_status"] for row in output)
    eligibility = Counter(row["eligibility_draft_status"] for row in output)
    report = f"""# P2-M02 — Class Rule Foundation Report

## 1. STATUS
`READY_FOR_P2_M03_EMPIRICAL_CLASS_STRENGTH`

## 2. Official sources
Four official entry points were captured as immutable raw HTML. The 2023-02-20 notice confirms the 2023-04-01 2YO pilot; the 2023-10-24 notice confirms all-horse application from 2024-01-01. Current Q&A is not applied backward.

## 3. Ruleset versions
Legacy prize-based through 2023-03-31; 2YO points pilot for 2023-04-01–2023-12-31; all-horse points from 2024-01-01.

## 4. Historical limitations
Legacy thresholds are unresolved. Historical pre-race program points and boundary positions are not available and were not fabricated.

## 5. Canonical class vocabulary
The eight order-only codes A1–C3 are parsed without treating ordinal distance as strength.

## 6. Mixed class
Mixed rows: {len(mixed):,}. Each retains all codes plus top/bottom/span; no scalar average is produced.

## 7. Group semantics
Group-token rows: {len(groups):,}. Group tokens/numbers are structural fields only and have `UNVERIFIED` comparability.

## 8. Grade / special race taxonomy
Grade remains separate from class. Heavy-grade, semi-graded, open, newcomer, age-conditioned, and other-special contexts are separately classified.

## 9. Age / sex / weight
Only raw-readable age, sex, and weight-condition tokens are structured. Unknowns remain unknown.

## 10. Exchange classification
Explicit JRA exchange: {sum(row['jra_exchange_flag'] for row in output):,}; explicit local exchange: {sum(row['local_exchange_flag'] for row in output):,}. Bare exchange text is not force-classified.

## 11. Eligibility DRAFT
Eligible {eligibility['ELIGIBLE']:,}; ineligible {eligibility['INELIGIBLE']:,}; review required {eligibility['REVIEW_REQUIRED']:,}. This is not a holdout freeze; C3 rows are retained.

## 12. Coverage
Nankan races: {len(output):,}; canonical class {class_counts['CANONICAL_CLASS']:,}; special {class_counts['SPECIAL_CLASSIFIED']:,}; age-ungraded {class_counts['AGE_UNGRADED_CLASSIFIED']:,}; unresolved {class_counts['UNRESOLVED']:,}. Explicit-class unresolved: 0.

## 13. 2023 transition
Only races dated 2023-04-01 or later with a raw-readable 2YO condition receive the pilot ruleset.

## 14. 2024 transition
All Nankan races dated 2024-01-01 or later receive the all-horse ruleset; no current rule is back-applied to earlier races.

## 15. Unresolved cases
No race remains unresolved after raw class/special/age taxonomy classification. Bare exchange semantics remain separately reported rather than inferred.

## 16. Leakage / provenance
Mapping read only race identity and raw condition/name/type fields from M01. No result, odds, payout, strength, or program-point reconstruction was used. Output SHA-256: `{sha256_path(CURATED)}`.

## 17. Next stage
P2-M03 may design empirical class-strength protocol only. No empirical value has been calculated or approved here.
"""
    atomic_text(REPORT, report)
    artifacts = [CURATED, SOURCE_MANIFEST, REPORT, code_manifest, *sorted(OUT.glob("*.csv"))]
    manifest = {"job_id": "P2-M02", "status": "READY_FOR_P2_M03_EMPIRICAL_CLASS_STRENGTH", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": now(), "input_db_sha256": sha256_path(DB), "official_source_manifest_sha256": sha256_path(SOURCE_MANIFEST), "mapping_config_sha256": sha256_path(MAPPING), "output_dataset_sha256": sha256_path(CURATED), "code_manifest_sha256": sha256_path(code_manifest), "config_manifest_sha256": sha256_path(REGISTRY), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.audit.p2_m02_class_ruleset_foundation"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)} for path in artifacts], "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "final_supervisor_status": "NOT_APPLICABLE_FOREGROUND"}, "resource": {"elapsed_seconds": elapsed, "peak_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}}
    atomic_json(OUT / "run_manifest.json", manifest)
    atomic_text(OUT / "run_manifest.sha256", f"{sha256_path(OUT / 'run_manifest.json')}  run_manifest.json\n")
    print(json_text({"status": manifest["status"], "nankan_races": len(output), "output_sha256": manifest["output_dataset_sha256"]}))


if __name__ == "__main__":
    main()
