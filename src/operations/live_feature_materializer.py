"""P7 strict-as-of FS04 materialization from a retained pre-decision card.

This module has no result/reconciliation database dependency.  It adapts the
official card and the exact T15 Market/CURRENT roster into the existing online
feature builders; feature logic itself remains in the frozen V1/M03/M04/M05
implementations.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.audit import p2_m02_class_ruleset_foundation as m02
from src.audit import p2_m07_target_universe as target_universe
from src.features.course_direction import resolve_current_target_direction
from src.features.legacy_v1.builder import build_online_legacy_features
from src.features.legacy_v1.contracts import LEGACY_FEATURES
from src.features.online.class_features import CLASS_FIELDS, build_online_class_features
from src.features.online.normalized_history_provider import P2NormalizedHistoricalAsOfProvider
from src.features.online.pace_features import PACE_FIELDS, build_online_pace_features
from src.features.online.race_class_text_adapter import m02_source_text
from src.features.online.speed_features import SPEED_FIELDS, build_online_speed_features
from src.features.online.v1_person_category import resolve_pre_race_v1_person_tokens
from src.ingestion.adapters import nankan_official as official
from src.operations.build_normalized_live_history_delta import _card_static_rows, _race_type_raw
from src.operations.normalize_live_history_delta import assert_normalized_fresh
from src.operations.official_pedigree_identity import PedigreeIdentityError, resolve_live_pre_race_identity
from src.operations.pre_race_fallback import select_pre_race_reference

ROOT = Path(__file__).resolve().parents[2]
MARKET_DB = ROOT / "db" / "market_snapshot.sqlite"
BASE_DB = ROOT / "db" / "p2_history_context.sqlite"
STATIC = ROOT / "data" / "curated" / "p2_legacy_v1" / "p2_v1_legacy_static_horse_semantics.csv.gz"


class LiveFeatureMaterializationError(RuntimeError):
    """A pre-race input contract did not establish an FS04 target safely."""


def _race_key(identity: dict[str, Any]) -> str:
    return f"P2_RACE_V1::{identity['race_date']}\x1f{identity['venue']}\x1f{int(identity['race_number'])}"


def _horse_key(name: str, birth_date: str) -> str:
    return "P2H_" + hashlib.sha256(f"{name}\x1f{birth_date}".encode("utf-8")).hexdigest()


def _target_card_rows(
    html: str, *, field_size: int, active_horse_numbers: set[int] | None = None
) -> dict[int, dict[str, Any]]:
    """Read only pre-race card fields needed by the existing target contracts."""
    root = official.parse_html(html)
    candidates = []
    for table in official.iter_nodes(root, "table"):
        headers = [official.node_text(cell) for cell in official.iter_nodes(table, "th")]
        if "負担重量" not in headers or "馬番" not in headers or "枠番" not in headers:
            continue
        rows: dict[int, dict[str, Any]] = {}
        for tr in official.iter_nodes(table, "tr"):
            cells = [cell for cell in official.direct_cells(tr) if cell.tag == "td"]
            values = [official.node_text(cell) for cell in cells]
            selector_positions = [
                (index, match) for index, value in enumerate(values)
                for match in [re.fullmatch(r"writeOdds\((\d+)\);", value)] if match is not None
            ]
            if len(selector_positions) != 1:
                continue
            selector_index, selector = selector_positions[0]
            horse_number = int(selector.group(1))
            # The official card marks an exact ``取消`` before any target
            # runtime fields are read.  Do not attempt to parse the changed
            # cancelled-row layout as an active runner.
            if active_horse_numbers is not None and horse_number not in active_horse_numbers:
                continue
            # The card can omit a redundant horse-number cell for a runner
            # whose frame equals horse number.  The exact writeOdds selector
            # remains the official roster binding; weight is always the second
            # displayed cell after it (body-weight/change then assigned weight).
            if selector_index + 2 >= len(values):
                raise LiveFeatureMaterializationError(f"P7_CARD_ASSIGNED_WEIGHT_CELL_UNRESOLVED:{horse_number}")
            frame = values[0]
            weight = values[selector_index + 2]
            if not re.fullmatch(r"\d+", frame):
                raise LiveFeatureMaterializationError(f"P7_CARD_FRAME_UNRESOLVED:{horse_number}:{frame!r}")
            match = re.fullmatch(r"[▲△◇☆]?([0-9]+(?:\.[0-9]+)?)", weight)
            if match is None:
                raise LiveFeatureMaterializationError(f"P7_CARD_ASSIGNED_WEIGHT_UNRESOLVED:{horse_number}:{weight!r}")
            rows[horse_number] = {"frame_number": int(frame), "assigned_weight": float(match.group(1))}
        if len(rows) >= field_size:
            candidates.append(rows)
    if not candidates:
        raise LiveFeatureMaterializationError("P7_CARD_TARGET_TABLE_UNRESOLVED")
    chosen = max(candidates, key=len)
    if len(chosen) != field_size:
        raise LiveFeatureMaterializationError(f"P7_CARD_TARGET_ROSTER_COUNT:{len(chosen)}:{field_size}")
    return chosen


def _validate_t15_active_roster(
    *, active_horse_numbers: set[int], withdrawn_horse_numbers: set[int],
    current_horse_numbers: set[int], market_horse_numbers: set[int],
) -> None:
    """Require exact T15 roster agreement without silently intersecting rows."""
    if withdrawn_horse_numbers & (current_horse_numbers | market_horse_numbers):
        raise LiveFeatureMaterializationError("T15_WITHDRAWN_ROSTER_CONFLICT")
    if current_horse_numbers != market_horse_numbers:
        raise LiveFeatureMaterializationError("P7_T15_ACTIVE_ROSTER_MISMATCH")
    if current_horse_numbers != active_horse_numbers:
        raise LiveFeatureMaterializationError("P7_T15_CARD_CURRENT_ROSTER_MISMATCH")


def _active_card_roster(
    *, statuses: dict[int, dict[str, Any]], card_static: dict[int, dict[str, Any]],
    card_runtime: dict[int, dict[str, Any]], people: dict[int, dict[str, Any]],
) -> tuple[set[int], dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Filter target inputs to exact ``ACTIVE`` card rows only.

    Status classification is shared with the current-card parser.  Static
    provenance for a withdrawn row is deliberately retained by ``statuses``;
    it simply never enters an FS04 target record.
    """
    active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    if not active <= set(card_static) or not active <= set(card_runtime) or not active <= set(people):
        raise LiveFeatureMaterializationError("P7_T15_ACTIVE_CARD_COMPONENT_MISMATCH")
    return (
        active,
        {number: card_static[number] for number in active},
        {number: card_runtime[number] for number in active},
        {number: people[number] for number in active},
    )


def _legacy_t15_input(*, race_date: str, venue: str, race_number: int, market_db: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    con = sqlite3.connect(f"file:{market_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        races = con.execute(
            "SELECT * FROM race_registry WHERE race_date=? AND venue=? AND race_number=?",
            (race_date, venue, race_number),
        ).fetchall()
        if len(races) != 1:
            raise LiveFeatureMaterializationError(f"P7_RACE_REGISTRY_EXACT_MATCH:{len(races)}")
        race = dict(races[0])
        snapshots = con.execute(
            """SELECT * FROM current_info_snapshots WHERE race_registry_id=? AND snapshot_mark='T15'
               AND t15_timing_status='PREDECISION_VALID'""",
            (race["race_registry_id"],),
        ).fetchall()
        if len(snapshots) != 1:
            raise LiveFeatureMaterializationError(f"P7_T15_PREDECISION_VALID:{len(snapshots)}")
        snapshot = dict(snapshots[0])
        current = [dict(row) for row in con.execute(
            "SELECT * FROM current_runner_info WHERE current_snapshot_id=? ORDER BY horse_number",
            (snapshot["current_snapshot_id"],),
        )]
        capture = con.execute("SELECT raw_archive_path FROM source_captures WHERE capture_id=?", (snapshot["raw_capture_id"],)).fetchall()
        if len(capture) != 1 or not capture[0][0]:
            raise LiveFeatureMaterializationError("P7_T15_OFFICIAL_CARD_RAW_UNRESOLVED")
        try:
            snapshot_notes = json.loads(snapshot.get("notes") or "{}")
        except json.JSONDecodeError as exc:
            raise LiveFeatureMaterializationError("P7_T15_CURRENT_NOTES_INVALID") from exc
        if not isinstance(snapshot_notes, dict):
            raise LiveFeatureMaterializationError("P7_T15_CURRENT_NOTES_INVALID")
        win_captures = con.execute(
            """SELECT DISTINCT capture_id FROM market_snapshots WHERE race_registry_id=? AND bet_type_code='WIN'
               AND snapshot_role='PRIMARY_CANDIDATE' AND target_decision_time='T-15_ENGINEERING_CANDIDATE'""",
            (race["race_registry_id"],),
        ).fetchall()
        configured_win_capture = snapshot_notes.get("market_win_capture_id") or snapshot_notes.get("market_capture_id")
        if configured_win_capture:
            win_capture_ids = [row[0] for row in win_captures]
            if configured_win_capture not in win_capture_ids:
                raise LiveFeatureMaterializationError("P7_T15_WIN_CAPTURE_NOT_IN_CURRENT_SNAPSHOT")
            selected_win_capture = str(configured_win_capture)
        elif len(win_captures) == 1:
            # Backward-compatible retained captures from before a current
            # snapshot explicitly recorded the per-bet-type capture IDs.
            selected_win_capture = str(win_captures[0][0])
        else:
            raise LiveFeatureMaterializationError(f"P7_T15_WIN_CAPTURE_EXACT:{len(win_captures)}")
        win = [dict(row) for row in con.execute(
            """SELECT snapshot_id,capture_id,CAST(normalized_combination_key AS INTEGER) AS horse_number,odds_value FROM market_snapshots
               WHERE race_registry_id=? AND bet_type_code='WIN' AND snapshot_role='PRIMARY_CANDIDATE'
               AND target_decision_time='T-15_ENGINEERING_CANDIDATE' AND capture_id=? ORDER BY horse_number""",
            (race["race_registry_id"], selected_win_capture),
        )]
        if not current or not win:
            raise LiveFeatureMaterializationError("P7_T15_CURRENT_OR_WIN_MISSING")
        if any(row["odds_value"] is None or float(row["odds_value"]) <= 0 for row in win):
            raise LiveFeatureMaterializationError("P7_T15_WIN_ODDS_INVALID")
        # WIDE is deliberately optional here: the fixed WIN policy remains
        # usable when a same-collection official WIDE capture is absent or
        # malformed.  It may never be substituted with a later/latest WIDE
        # page.  The current snapshot note is the capture-set boundary.
        configured_wide_capture = snapshot_notes.get("market_wide_capture_id")
        wide_status = str(snapshot_notes.get("market_wide_status") or "WIDE_MARKET_INCOMPLETE")
        wide_rows: list[dict[str, Any]] | None = None
        wide_provenance: dict[str, Any] = {
            "current_snapshot_id": snapshot["current_snapshot_id"],
            "win_capture_id": selected_win_capture,
            "wide_capture_id": configured_wide_capture,
            "selection_rule": "EXACT_CURRENT_T15_CAPTURE_SET_NOT_LATEST",
            "status": wide_status,
        }
        if configured_wide_capture:
            rows = [dict(row) for row in con.execute(
                """SELECT snapshot_id,capture_id,normalized_combination_key,odds_value AS lower_odds,
                          max_odds_value AS upper_odds
                   FROM market_snapshots
                  WHERE race_registry_id=? AND bet_type_code='WIDE' AND snapshot_role='PRIMARY_CANDIDATE'
                    AND target_decision_time='T-15_ENGINEERING_CANDIDATE' AND capture_id=?
                  ORDER BY normalized_combination_key""",
                (race["race_registry_id"], configured_wide_capture),
            )]
            adapted: list[dict[str, Any]] = []
            for row in rows:
                parts = str(row.get("normalized_combination_key") or "").split("-")
                if len(parts) == 2 and all(part.isdigit() for part in parts):
                    adapted.append(row | {"horse_number_1": int(parts[0]), "horse_number_2": int(parts[1])})
                else:
                    # Feed the exact malformed source representation to the
                    # WIDE validator, which returns a WIDE-only fail-closed
                    # status without affecting WIN.
                    adapted.append(row | {"horse_number_1": None, "horse_number_2": None})
            wide_rows = adapted
            wide_provenance["actual_pair_rows"] = len(adapted)
        else:
            wide_provenance["reason"] = "no WIDE capture recorded for this exact current snapshot"
        return race | {
            "t15_snapshot": snapshot,
            "t15_win_rows": win,
            "t15_wide_rows": wide_rows,
            "t15_wide_snapshot_provenance": wide_provenance,
        }, current, str(capture[0][0])
    finally:
        con.close()


def _t15_input(
    *, race_date: str, venue: str, race_number: int, market_db: Path,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Return the shared standard/fallback reference as the legacy P7 shape."""
    selected = select_pre_race_reference(
        db_path=market_db, race_date=race_date, venue=venue, race_number=race_number, now=now,
    )
    if selected.get("status") != "READY":
        raise LiveFeatureMaterializationError(
            f"P7_PRE_RACE_REFERENCE_UNAVAILABLE:{selected.get('reason', selected.get('status'))}"
        )
    snapshot = selected["snapshot"]
    reference = selected["reference"] | {"policy_file_sha256": selected["policy_sha256"]}
    registry = selected["race"] | {
        # Retain the historical key for downstream/P8 compatibility.  Its
        # contents can now be T15_STANDARD or PRE_RACE_FALLBACK.
        "t15_snapshot": snapshot,
        "t15_win_rows": selected["t15_win_rows"],
        "t15_wide_rows": selected["t15_wide_rows"],
        "t15_wide_snapshot_provenance": selected["t15_wide_snapshot_provenance"],
        "t15_trio_rows": selected["t15_trio_rows"],
        "t15_trio_snapshot_provenance": selected["t15_trio_snapshot_provenance"],
        "predecision_reference": reference,
    }
    return registry, selected["current_rows"], selected["raw_card_path"]


def _class_name(name: str) -> str:
    if name in {"ruleset_id", "class_top_code", "class_bottom_code", "class_top_ordinal", "class_bottom_ordinal", "mixed_class_flag", "race_taxonomy_code", "race_grade_code"}:
        return f"P2_CLASS_RULE__{name}"
    if name in {"rating_pre", "field_strength_shrunk_mean", "runner_strength_delta", "race_strength_delta", "official_class_top_step", "official_class_bottom_step", "official_class_direction"}:
        return f"P2_CLASS_EMPIRICAL__{name}"
    return f"P2_CLASS_UNCERTAINTY__{name}"


def materialize_t15_fs04(*, race_date: str, venue: str, race_number: int, market_db: Path = MARKET_DB, now: datetime | None = None) -> dict[str, Any]:
    """Materialize FS04 from T15_STANDARD or the selected pre-race fallback."""
    if race_date > "2026-07-31":
        try:
            assert_normalized_fresh(target_date=race_date)
        except RuntimeError as exc:
            raise LiveFeatureMaterializationError(str(exc)) from exc
    registry, current, raw_path = _t15_input(race_date=race_date, venue=venue, race_number=race_number, market_db=market_db, now=now)
    html = official.decode_html((ROOT / raw_path).read_bytes())
    identity = official.parse_race_identity(html)
    expected = (race_date, venue, int(race_number))
    if (identity["race_date"], identity["venue"], identity["race_number"]) != expected:
        raise LiveFeatureMaterializationError("P7_T15_CARD_RACE_IDENTITY_MISMATCH")
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    active_numbers = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    withdrawn_numbers = set(statuses) - active_numbers
    if len(current) != len(active_numbers):
        raise LiveFeatureMaterializationError(f"P7_T15_FIELD_SIZE_MISMATCH:{len(current)}:{len(active_numbers)}")
    all_card_static = _card_static_rows(html, identity)
    all_card_runtime = _target_card_rows(html, field_size=len(active_numbers), active_horse_numbers=active_numbers)
    all_people = resolve_pre_race_v1_person_tokens(html, identity=identity)
    active_numbers, card_static, card_runtime, people = _active_card_roster(
        statuses=statuses, card_static=all_card_static, card_runtime=all_card_runtime, people=all_people
    )
    current_by_number = {int(row["horse_number"]): row for row in current}
    market_numbers = {int(row["horse_number"]) for row in registry["t15_win_rows"]}
    _validate_t15_active_roster(
        active_horse_numbers=active_numbers, withdrawn_horse_numbers=withdrawn_numbers,
        current_horse_numbers=set(current_by_number), market_horse_numbers=market_numbers,
    )
    resolved_identities: dict[int, dict[str, str]] = {}
    for horse_number in sorted(current_by_number):
        current_row, static = current_by_number[horse_number], card_static[horse_number]
        if current_row.get("horse_name_exact") != static["horse_name_exact"]:
            raise LiveFeatureMaterializationError(f"P7_T15_HORSE_NAME_CONFLICT:{horse_number}")
        try:
            # This is the shared R1/R7 resolver.  It resolves only from this
            # retained official pre-race card, the linked official detail page
            # (if any), and the approved canonical master.
            resolved_identities[horse_number] = resolve_live_pre_race_identity(
                static, birth_date_raw=current_row.get("birth_date_raw")
            )
        except PedigreeIdentityError as exc:
            raise LiveFeatureMaterializationError(f"P7_T15_HORSE_IDENTITY_UNRESOLVED:{horse_number}:{exc}") from exc
    direction = resolve_current_target_direction(venue=venue, distance_m=int(identity["distance_m"]))
    race_key = _race_key(identity)
    raw_type = _race_type_raw(html, race_key)
    class_source = {
        "race_key": race_key, "race_date": race_date, "venue": venue, "race_number": int(race_number),
        "conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"),
        "race_type_raw": m02_source_text(raw_type), "venue_class": "NANKAN_TARGET",
    }
    class_row = m02.classify(class_source)
    if class_row.get("parse_status") == "UNRESOLVED":
        raise LiveFeatureMaterializationError(f"P7_T15_CLASS_UNRESOLVED:{raw_type}")
    class_row.update({"race_key": race_key, "race_type_raw": raw_type, "race_class_text_m02": class_source["race_type_raw"]})
    primary_status, primary_reason = target_universe.classify_race(class_row | {
        "conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"), "race_type_raw": raw_type,
    })
    v1_targets: list[dict[str, Any]] = []
    basic_targets: list[dict[str, Any]] = []
    for horse_number in sorted(current_by_number):
        current_row, static, runtime, person = current_by_number[horse_number], card_static[horse_number], card_runtime[horse_number], people[horse_number]
        resolved = resolved_identities[horse_number]
        horse_identity_key = resolved["horse_identity_key"]
        basic = {
            "race_key": race_key, "race_date": race_date, "venue": venue, "race_number": int(race_number),
            "surface": identity["surface"], "direction": direction["direction"], "distance_m": int(identity["distance_m"]),
            "field_size": len(active_numbers), "horse_identity_key": horse_identity_key,
            "frame_number": runtime["frame_number"], "horse_number": horse_number,
        }
        basic_targets.append(basic)
        v1_targets.append(basic | {
            "jockey": person["jockey_v1_token"], "trainer": person["trainer_v1_token"],
            "assigned_weight": runtime["assigned_weight"], "body_weight": current_row["body_weight_kg"],
            "birth_date": resolved["birth_date"], "sex": static["sex"], "sire": static["sire"], "damsire": static["damsire"],
        })
    provider = P2NormalizedHistoricalAsOfProvider(race_date)
    v1, v1_audit = build_online_legacy_features(str(BASE_DB), v1_targets, str(STATIC), history_records=provider.v1_history_asof())
    klass = build_online_class_features([{
        "race_key": race_key, "race_date": race_date, "venue": venue, "race_number": int(race_number),
        "field_size": int(identity["field_size"]), "class_row": class_row,
        "runners": [{"horse_identity_key": row["horse_identity_key"], "horse_number": row["horse_number"]} for row in basic_targets],
    }], history_provider=provider)
    speed = build_online_speed_features(basic_targets, history_provider=provider)
    pace = build_online_pace_features(basic_targets, history_provider=provider)
    blocks = [{(row["race_key"], int(row["horse_number"])): row for row in values} for values in (v1, klass, speed, pace)]
    keys = set(blocks[0])
    if any(set(block) != keys for block in blocks[1:]):
        raise LiveFeatureMaterializationError("P7_FS04_BLOCK_ROSTER_MISMATCH")
    rows = []
    for key in sorted(keys, key=lambda value: value[1]):
        left, middle, right, last = (block[key] for block in blocks)
        rows.append({
            "race_key": key[0], "race_date": race_date, "venue": venue, "horse_identity_key": left["horse_identity_key"], "horse_number": key[1],
            **{f"V1__{name}": left[name] for name in LEGACY_FEATURES},
            **{_class_name(name): middle[name] for name in CLASS_FIELDS},
            **{f"P2_SPD__{name}": right[name] for name in SPEED_FIELDS},
            **{f"P2_PACE__{name}": last[name] for name in PACE_FIELDS},
        })
    features = json.loads((ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json").read_text(encoding="utf-8"))["ordered_feature_names"]
    if len(features) != 178 or any([name for name in features if name not in rows[0]]):
        raise LiveFeatureMaterializationError("P7_FS04_FEATURE_CONTRACT_MISMATCH")
    counts = provider.counts()
    if counts["same_day_rows_visible"] or counts["max_history_date"] is not None and counts["max_history_date"] >= race_date:
        raise LiveFeatureMaterializationError("P7_FS04_SAME_DAY_HISTORY_LEAKAGE")
    return {
        "identity": identity | {"race_key": race_key, "direction_source": direction["direction_source_status"], "active_field_size": len(active_numbers)},
        "primary_eligibility": {"status": primary_status, "reason": primary_reason},
        "t15_snapshot": registry["t15_snapshot"], "t15_snapshot_parent": registry,
        "predecision_reference": registry["predecision_reference"], "raw_card_path": raw_path,
        "feature_names": features, "rows": rows, "provider_counts": counts, "v1_audit": v1_audit,
        "identity_audit": [
            {"horse_number": number, "identity_status": "RESOLVED", **resolved_identities[number]}
            for number in sorted(resolved_identities)
        ],
        "pre_race_withdrawal_audit": [
            {
                "race_key": race_key,
                "source_capture_id": registry["t15_snapshot"]["raw_capture_id"],
                "source_capture_path": raw_path,
                **status,
                "identity_resolution_status": "NOT_REQUIRED_FOR_TARGET_FEATURE",
            }
            for number, status in sorted(statuses.items())
            if number in withdrawn_numbers
        ],
        "result_db_accessed": 0,
    }


def score_dev_live_v1(materialized: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply the frozen DEV-LIVE-V1 artifact; no training or model search."""
    import lightgbm
    from src.market.normalization import normalize_win_odds
    from src.models.market_offset.prediction import predict_win_market_offset
    from src.models.market_offset.preprocessing import FoldSafePreprocessor

    model_dir = ROOT / "models" / "development" / "dev_live_v1"
    preprocessing = json.loads((model_dir / "preprocessing.json").read_text(encoding="utf-8"))
    names = materialized["feature_names"]
    if names != preprocessing["feature_names"] or len(names) != 178:
        raise LiveFeatureMaterializationError("P7_MODEL_FEATURE_HASH_OR_ORDER_MISMATCH")
    categorical = set(preprocessing["categorical_indices"])
    specs = [{"phase2_integrated_name": name, "dtype": "categorical" if index in categorical else "numeric"} for index, name in enumerate(names)]
    preprocessor = FoldSafePreprocessor(specs)
    preprocessor.category_maps = preprocessing["category_maps"]
    matrix = preprocessor.transform(materialized["rows"])
    if len(matrix) != len(materialized["rows"]) or any(len(row) != 178 for row in matrix):
        raise LiveFeatureMaterializationError("P7_MODEL_MATRIX_SHAPE")
    booster = lightgbm.Booster(model_file=str(model_dir / "model.txt"))
    if booster.num_feature() != 178:
        raise LiveFeatureMaterializationError("P7_MODEL_FEATURE_COUNT")
    residual = [float(value) for value in booster.predict(matrix, raw_score=True)]
    if not all(math.isfinite(value) for value in residual):
        raise LiveFeatureMaterializationError("P7_MODEL_NONFINITE_RESIDUAL")
    market = normalize_win_odds([
        {"horse_number": str(row["horse_number"]), "odds_win": row["odds_value"]}
        for row in materialized["t15_snapshot_parent"]["t15_win_rows"]
    ])
    by_number = {int(row["horse_number"]): row for row in market}
    if {int(row["horse_number"]) for row in materialized["rows"]} != set(by_number):
        raise LiveFeatureMaterializationError("P7_MODEL_MARKET_FEATURE_ROSTER_MISMATCH")
    model_rows = [
        {"race_key": row["race_key"], "horse_number": row["horse_number"],
         "q_raw": by_number[int(row["horse_number"])]["q_raw"],
         "log_q_raw": by_number[int(row["horse_number"])]["log_q_raw"]}
        for row in materialized["rows"]
    ]
    gamma = float(json.loads((model_dir / "gamma.json").read_text(encoding="utf-8"))["gamma"])
    output = predict_win_market_offset(model_rows, residual, gamma)
    if abs(math.fsum(row["market_calibrated_p"] for row in output) - 1.0) > 1e-12 or abs(math.fsum(row["candidate_probability"] for row in output) - 1.0) > 1e-12:
        raise LiveFeatureMaterializationError("P7_MODEL_PROBABILITY_SUM")
    return output
