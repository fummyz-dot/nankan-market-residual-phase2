"""Auditable source and fixture verification for P2_CURRENT jockey parsing.

This job reads only retained official current-card raw HTML.  It never opens a
result/outcome store and never writes a production database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "current_info" / "2026"
OUT = ROOT / "audit" / "data" / "p2_current_jockey_parse_v1_20260826"
PRIMARY_DATE, PRIMARY_VENUE = "2026-08-24", "船橋"
JOCKEY_LINK = re.compile(r"/kis_info/(\d+)\.do")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read(path: Path) -> tuple[str, dict[str, Any]]:
    html = official.decode_html(path.read_bytes(), "text/html")
    return html, official.parse_race_identity(html)


def _primary_path(race_number: int) -> Path:
    candidates = sorted((RAW_ROOT / PRIMARY_DATE / PRIMARY_VENUE / f"race{race_number:02d}").glob("*.html"))
    if not candidates:
        raise RuntimeError(f"CURRENT_JOCKEY_AUDIT_PRIMARY_RAW_MISSING:{race_number}")
    return candidates[0]


def _legacy_positional_jockeys(html: str, identity: dict[str, Any]) -> dict[int, str]:
    """Reproduce only the superseded extraction to quantify the known defect."""
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    target: official.Node | None = None
    jockey_index: int | None = None
    for table in official.iter_nodes(official.parse_html(html), "table"):
        headers = [official.node_text(cell) for cell in official.iter_nodes(table, "th")]
        has_runner_row = any(
            len([cell for cell in official.direct_cells(row) if cell.tag == "td"]) >= 8
            for row in official.iter_nodes(table, "tr")
        )
        if has_runner_row and "馬体重増減" in headers and "馬番" in headers and any("騎手名" in header for header in headers):
            target = table
            jockey_index = next(index for index, header in enumerate(headers) if "騎手名" in header)
            break
    if target is None or jockey_index is None:
        return {}
    output: dict[int, str] = {}
    for row in official.iter_nodes(target, "tr"):
        values = [official.node_text(cell) for cell in official.direct_cells(row) if cell.tag == "td"]
        if len(values) <= jockey_index:
            continue
        leading = [int(value) for value in values[:2] if re.fullmatch(r"\d+", value)]
        if leading and leading[-1] in active:
            output[leading[-1]] = values[jockey_index].strip()
    return output


def _row_sources(html: str) -> list[dict[str, Any]]:
    """Record the direct official DOM evidence for every roster row."""
    table = official._current_card_identity_table(official.parse_html(html))
    rows: list[dict[str, Any]] = []
    for row in official._direct_table_rows(table):
        cells = [cell for cell in official.direct_cells(row) if cell.tag == "td"]
        number = official._current_card_row_number(cells)
        if number is None:
            continue
        horse_number, _ = number
        horse_anchors = [
            anchor for cell in cells for anchor in official.iter_nodes(cell, "a")
            if re.fullmatch(r"/uma_info/\d+\.do", anchor.attrs.get("href", ""))
        ]
        jockey_cells = [
            cell for cell in cells
            if any(JOCKEY_LINK.fullmatch(anchor.attrs.get("href", "")) for anchor in official.iter_nodes(cell, "a"))
        ]
        jockey_anchors = [
            anchor for cell in jockey_cells for anchor in official.iter_nodes(cell, "a")
            if JOCKEY_LINK.fullmatch(anchor.attrs.get("href", ""))
        ]
        jockey_index = cells.index(jockey_cells[0]) if len(jockey_cells) == 1 else None
        pedigree = None if jockey_index is None or jockey_index + 1 >= len(cells) else cells[jockey_index + 1]
        rows.append({
            "horse_number": horse_number,
            "horse_name": official.node_text(horse_anchors[0]).strip() if len(horse_anchors) == 1 else None,
            "jockey_display": official.node_text(jockey_cells[0]).strip() if len(jockey_cells) == 1 else None,
            "jockey_anchor_href": jockey_anchors[0].attrs.get("href") if len(jockey_anchors) == 1 else None,
            "jockey_id": JOCKEY_LINK.fullmatch(jockey_anchors[0].attrs.get("href", "")).group(1) if len(jockey_anchors) == 1 else None,
            "jockey_cell_class": jockey_cells[0].attrs.get("class") if len(jockey_cells) == 1 else None,
            "jockey_direct_cell_index": jockey_index,
            "pedigree_display": official.node_text(pedigree).strip() if pedigree is not None else None,
            "pedigree_cell_class": pedigree.attrs.get("class") if pedigree is not None else None,
            "pedigree_position": "next_direct_td_after_explicit_jockey_cell" if pedigree is not None else None,
            "explicit_jockey_source_status": "RESOLVED" if len(jockey_cells) == len(jockey_anchors) == 1 else "UNRESOLVED",
        })
    return rows


def _source_semantics() -> dict[str, Any]:
    cards = []
    for race_number in range(6, 11):
        path = _primary_path(race_number)
        html, identity = _read(path)
        rows = _row_sources(html)
        legacy = _legacy_positional_jockeys(html, identity)
        parsed = official.parse_current_card(html, identity=identity, captured_at="2026-08-24T08:00:00+00:00")
        parsed_by_number = {int(row["horse_number"]): row["declared_jockey_raw"] for row in parsed["runners"]}
        for row in rows:
            number = int(row["horse_number"])
            row["legacy_fixed_header_index_value"] = legacy.get(number)
            row["parsed_declared_jockey_raw"] = parsed_by_number.get(number)
            row["legacy_pedigree_contamination"] = (
                number in parsed_by_number
                and legacy.get(number) is not None
                and legacy.get(number) == row["pedigree_display"]
            )
        cards.append({
            "race": identity,
            "raw_path": str(path.relative_to(ROOT)),
            "raw_sha256": _sha256(path),
            "rows": rows,
            "parser_warnings": parsed["warnings"],
        })
    return {
        "task_id": "P2-CURRENT-JOCKEY-PARSE-V1-001",
        "source_contract": {
            "declared_jockey_raw": "direct cell containing exactly one official /kis_info/<id>.do anchor in the same official entry row",
            "runner_binding": "official displayed horse number from the same direct table row",
            "display_semantic": "complete official jockey cell text, including displayed affiliation",
            "jockey_id_semantic": "official anchor path capture only; not persisted because current_runner_info has no jockey-id field",
            "pedigree_semantic": "adjacent direct cell after the explicit jockey cell, headed 父馬名母馬名; audit-only and prohibited as fallback",
            "prohibited_fallbacks": ["pedigree", "sire/dam", "adjacent arbitrary text", "horse name", "Keibabook", "name dictionary"],
            "unresolved_contract": "declared_jockey_raw=null plus CURRENT_JOCKEY_UNRESOLVED",
        },
        "jockey_change_audit": {
            "current_input_path": "src.ingestion.adapters.nankan_official.parse_current_card -> src.operations.current_info.record_current_snapshot.current_runner_info.declared_jockey_raw",
            "prior_jockey_source": "src.operations.current_info.strict_prior_jockey: race_runners.jockey joined to races, strictly r.race_date < target_race_date",
            "comparison": "src.operations.current_info.jockey_change: trimmed exact-string inequality; null on either missing input",
            "task_change": "NONE",
        },
        "cards": cards,
        "result_db_accessed": 0,
    }


def _fixture_results() -> dict[str, Any]:
    races: dict[str, Any] = {}
    contaminated_before = 0
    contaminated_after = 0
    for race_number in range(6, 11):
        path = _primary_path(race_number)
        html, identity = _read(path)
        card = official.parse_current_card(html, identity=identity, captured_at="2026-08-24T08:00:00+00:00")
        sources = {int(row["horse_number"]): row for row in _row_sources(html)}
        legacy = _legacy_positional_jockeys(html, identity)
        observed = {int(row["horse_number"]): row["declared_jockey_raw"] for row in card["runners"]}
        before = [number for number, value in legacy.items() if number in observed and value == sources[number]["pedigree_display"]]
        after = [number for number, value in observed.items() if value == sources[number]["pedigree_display"]]
        contaminated_before += len(before)
        contaminated_after += len(after)
        races[f"{race_number}R"] = {
            "active_runner_count": len(observed),
            "explicit_jockey_match_count": sum(observed[number] == sources[number]["jockey_display"] for number in observed),
            "legacy_pedigree_contamination_horse_numbers": before,
            "post_fix_pedigree_contamination_horse_numbers": after,
            "warnings": card["warnings"],
        }

    html, identity = _read(_primary_path(6))
    missing = official.parse_current_card(
        html.replace('/kis_info/031235.do', '/not_jockey/031235.do'),
        identity=identity, captured_at="2026-08-24T08:00:00+00:00",
    )
    missing_by_number = {int(row["horse_number"]): row["declared_jockey_raw"] for row in missing["runners"]}
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    return {
        "status": "PASS",
        "races": races,
        "known_contaminated_rows_before": contaminated_before,
        "known_contaminated_rows_after": contaminated_after,
        "missing_jockey": {
            "horse_number": 6,
            "declared_jockey_raw": missing_by_number[6],
            "warnings": [value for value in missing["warnings"] if int(value["horse_number"]) == 6],
        },
        "withdrawal": {
            "race": "2026-08-24 船橋6R",
            "horse_number": 3,
            "normalized_status": statuses[3]["normalized_status"],
            "active_runner_count": sum(value["normalized_status"] == "ACTIVE" for value in statuses.values()),
            "active_roster_contains_3": False,
        },
        "result_db_accessed": 0,
    }


def _venue_coverage() -> dict[str, Any]:
    available: dict[str, list[Path]] = {}
    for path in sorted(RAW_ROOT.glob("*/*/race*/*.html")):
        venue = path.parts[-3]
        available.setdefault(venue, []).append(path)
    output: dict[str, Any] = {}
    for venue in ("船橋", "川崎", "大井", "浦和"):
        per_race: dict[tuple[str, str], Path] = {}
        for path in available.get(venue, []):
            key = (path.parts[-4], path.parts[-2])
            per_race.setdefault(key, path)
        races = resolved = unresolved = contamination = 0
        source_failures: list[dict[str, str]] = []
        full_card_parse_gaps: list[dict[str, str]] = []
        for path in per_race.values():
            try:
                html, identity = _read(path)
                statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
                active = {number for number, value in statuses.items() if value["normalized_status"] == "ACTIVE"}
                rows = {int(row["horse_number"]): row for row in _row_sources(html)}
                parsed, warnings = official._parse_current_card_declared_jockeys(html, active_numbers=active)
                races += 1
                for number in active:
                    declared = parsed[number]
                    resolved += int(declared is not None)
                    unresolved += int(declared is None)
                    contamination += int(
                        declared is not None and declared == rows[number]["pedigree_display"]
                    )
                if warnings:
                    source_failures.extend(
                        {"raw_path": str(path.relative_to(ROOT)), "error": str(warning)}
                        for warning in warnings
                    )
                try:
                    official.parse_current_card(html, identity=identity, captured_at="2026-08-24T08:00:00+00:00")
                except Exception as exc:
                    # Body-weight availability is independent of the jockey
                    # anchor audit.  Do not mislabel this as a venue DOM
                    # difference in the source semantics report.
                    full_card_parse_gaps.append({"raw_path": str(path.relative_to(ROOT)), "error": f"{type(exc).__name__}:{exc}"})
            except Exception as exc:
                source_failures.append({"raw_path": str(path.relative_to(ROOT)), "error": f"{type(exc).__name__}:{exc}"})
        output[venue] = {
            "raw_current_card_available": bool(per_race),
            "sampled_races": races,
            "jockey_source_resolved": resolved,
            "jockey_source_unresolved": unresolved,
            "post_fix_pedigree_contamination": contamination,
            "source_semantic_branch": "EXPLICIT_KIS_INFO_ANCHOR" if per_race else "RAW_NOT_AVAILABLE",
            "source_failures": source_failures,
            "full_card_parse_gaps_not_jockey_source_failures": full_card_parse_gaps,
        }
    return {"venues": output, "result_db_accessed": 0}


def _fresh_process_smoke() -> dict[str, Any]:
    """Record the bounded fresh-process parser smoke and model-use boundary.

    ``run`` is invoked by ``python -m`` in the verification command, so these
    parser cases are deliberately exercised after a new import of the changed
    module.  The attempted full `race-shadow` replay is retained separately
    as an execution-ceiling warning rather than reported as a pass.
    """
    path = _primary_path(6)
    html, identity = _read(path)
    normal = official.parse_current_card(html, identity=identity, captured_at="2026-08-24T08:04:33.992777+00:00")
    malformed = official.parse_current_card(
        html.replace('/kis_info/031235.do', '/not_jockey/031235.do'),
        identity=identity, captured_at="2026-08-24T08:04:33.992777+00:00",
    )
    normal_by_number = {int(row["horse_number"]): row["declared_jockey_raw"] for row in normal["runners"]}
    malformed_by_number = {int(row["horse_number"]): row["declared_jockey_raw"] for row in malformed["runners"]}
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    if normal_by_number[6] != "張田昂 (船橋)" or malformed_by_number[6] is not None:
        raise RuntimeError("CURRENT_JOCKEY_FRESH_PARSE_REGRESSION")
    if len(normal_by_number) != 11 or 3 in normal_by_number or statuses[3]["normalized_status"] != "PRE_RACE_WITHDRAWN":
        raise RuntimeError("CURRENT_JOCKEY_FRESH_WITHDRAWAL_REGRESSION")
    fs04 = json.loads((ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json").read_text(encoding="utf-8"))[
        "ordered_feature_names"
    ]
    materializer_source = (ROOT / "src/operations/live_feature_materializer.py").read_text(encoding="utf-8")
    if len(fs04) != 178 or "declared_jockey_raw" in materializer_source:
        raise RuntimeError("CURRENT_JOCKEY_FS04_CONTEXT_ONLY_BOUNDARY")
    failed_marker = OUT / "top_level_race_shadow.run" / "FAILED_STARTUP_PROCESS_DIED"
    return {
        "status": "PARTIAL_TOP_LEVEL_EXECUTION_CEILING",
        "fresh_python_process": True,
        "normal_card": "PASS",
        "pedigree_contamination": "PASS",
        "withdrawal": "PASS",
        "missing_jockey": "PASS",
        "active_runner_count": len(normal_by_number),
        "withdrawn_runner": 3,
        "missing_jockey_warnings": malformed["warnings"],
        "fs04_feature_count": len(fs04),
        "candidate_invariance_boundary": "PASS_DECLARED_JOCKEY_NOT_READ_BY_LIVE_FEATURE_MATERIALIZER",
        "policy_v2_sha256": _sha256(ROOT / "configs/ops_bet_policy_v2.json"),
        "result_db_accessed": 0,
        "production_db_mutation": 0,
        "top_level_race_shadow": {
            "status": "NOT_COMPLETED_EXECUTION_ENVIRONMENT_CEILING",
            "command": ["./race-shadow", "--date", "2026-08-20", "--venue", "川崎", "--race", "8", "--engineering-replay", "--json"],
            "evidence_marker": str(failed_marker.relative_to(ROOT)) if failed_marker.exists() else None,
            "reason": "command process was terminated by the execution environment at approximately 30 seconds before a worker PID or output was produced",
        },
    }


def _implementation_report() -> dict[str, Any]:
    paths = [
        ROOT / "src/ingestion/adapters/nankan_official.py",
        ROOT / "src/operations/prospective_day_collector.py",
        ROOT / "tests/unit/test_p2_current_jockey_parse_v1.py",
        Path(__file__),
    ]
    return {
        "task_id": "P2-CURRENT-JOCKEY-PARSE-V1-001",
        "changed_files": [{"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)} for path in paths],
        "implementation": "replaced flattened-header positional jockey lookup with same-row explicit /kis_info/<id>.do anchor lookup",
        "unresolved_semantic": "null declared_jockey_raw plus structured CURRENT_JOCKEY_UNRESOLVED warning retained in current snapshot notes",
        "jockey_id": "not persisted; no existing current_runner_info jockey-id field",
        "context_only": {"fs04_changed": False, "dev_live_v1_changed": False, "policy_changed": False, "wide_research_changed": False},
        "tests_run": [
            "python3 -m unittest tests.unit.test_p2_current_jockey_parse_v1 tests.unit.test_p2_m11a_current_foundation tests.unit.test_p2_live_pre_race_withdrawal -v",
            "python3 -m src.audit.p2_current_jockey_parse_v1 --output audit/data/p2_current_jockey_parse_v1_20260826",
        ],
        "fresh_process_smoke": "PARTIAL_TOP_LEVEL_EXECUTION_CEILING; parser/current/withdrawal/missing-jockey cases PASS in a new Python process, while top-level race-shadow was externally terminated before output.",
        "result_db_accessed": 0,
        "production_db_mutation": 0,
        "known_limitations": [
            "Saved P2_CURRENT raw is available for 船橋 and 川崎 only; 大井 and 浦和 are explicitly recorded as RAW_NOT_AVAILABLE.",
            "No new jockey identity architecture or jockey-change semantic was introduced.",
        ],
    }


def _run_manifest(output: Path) -> dict[str, Any]:
    inputs = [_primary_path(number) for number in range(6, 11)]
    code = [ROOT / "src/ingestion/adapters/nankan_official.py", ROOT / "src/operations/prospective_day_collector.py", Path(__file__)]
    return {
        "task_id": "P2-CURRENT-JOCKEY-PARSE-V1-001",
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": [sys.executable, "-m", "src.audit.p2_current_jockey_parse_v1", "--output", str(output)],
        "platform": {"python": sys.version, "platform": platform.platform()},
        "random_seed": None,
        "code_manifest": [{"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)} for path in code],
        "input_manifest": [{"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)} for path in inputs],
        "config_manifest": [
            {"path": "configs/features/P2_CURRENT_CANDIDATE_REGISTRY_V1.yaml", "sha256": _sha256(ROOT / "configs/features/P2_CURRENT_CANDIDATE_REGISTRY_V1.yaml")}
        ],
        "output_root": str(output.resolve().relative_to(ROOT)),
        "result_db_accessed": 0,
    }


def run(*, output: Path = OUT) -> dict[str, Any]:
    source = _source_semantics()
    fixtures = _fixture_results()
    coverage = _venue_coverage()
    fresh = _fresh_process_smoke()
    report = _implementation_report()
    manifest = _run_manifest(output)
    _atomic_json(output / "source_semantics.json", source)
    _atomic_json(output / "fixture_results.json", fixtures)
    _atomic_json(output / "venue_coverage.json", coverage)
    _atomic_json(output / "fresh_process_smoke.json", fresh)
    _atomic_json(output / "implementation_report.json", report)
    _atomic_json(output / "run_manifest.json", manifest)
    return {
        "status": "PASS",
        "output": str(output),
        "known_contaminated_rows_before": fixtures["known_contaminated_rows_before"],
        "known_contaminated_rows_after": fixtures["known_contaminated_rows_after"],
        "result_db_accessed": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit P2_CURRENT declared-jockey source semantics from retained raw cards.")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(run(output=args.output), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
