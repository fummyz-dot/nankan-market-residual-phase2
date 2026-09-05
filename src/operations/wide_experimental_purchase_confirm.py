"""Explicit user-only purchase evidence for immutable WIDE Experimental intents."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INTENT_ROOT = ROOT / "outputs" / "live_development" / "wide_experimental_v0" / "intents"
OHI_INTENT_ROOT = ROOT / "outputs" / "live_development" / "wide_ohi_experimental_v0" / "intents"
OUT = ROOT / "outputs" / "live_development" / "wide_experimental_purchase_confirmations"
EVIDENCE_DB = ROOT / "db" / "live_development.sqlite"
COMPONENT_ID = "P2_WIDE_EXPERIMENTAL_PURCHASE_CONFIRM_V0"
CONFIRMATION_VERSION = "p2_wide_experimental_purchase_confirm_v0"
PURCHASED = "PURCHASED"
NOT_PURCHASED = "NOT_PURCHASED"
RECOGNIZED_POLICIES = {
    "P2_WIDE_FUNABASHI_EXPERIMENTAL_V0": {
        "p2_wide_funabashi_experimental_v0_intent_v1",
    },
    "P2_WIDE_OHI_EXPERIMENTAL_V0": {
        "p2_wide_ohi_experimental_v0_intent_v1",
    },
}


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("PURCHASE_CONFIRMATION_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _resolve_intent_path(value: Path) -> Path:
    candidate = value if value.is_absolute() else ROOT / value
    resolved = candidate.resolve()
    for root in (INTENT_ROOT, OHI_INTENT_ROOT):
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError("PURCHASE_CONFIRMATION_INTENT_PATH_OUTSIDE_AUTHORITATIVE_ROOT")


def _policy_intent_root(policy_id: str) -> Path:
    return INTENT_ROOT if policy_id == "P2_WIDE_FUNABASHI_EXPERIMENTAL_V0" else OHI_INTENT_ROOT


def _read_intent(path: Path) -> tuple[dict[str, Any], bytes] | None:
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return (parsed, raw) if isinstance(parsed, dict) else None


def _valid_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number == value else None


def _validate_intent(intent: dict[str, Any]) -> str | None:
    policy = intent.get("policy_id")
    if policy not in RECOGNIZED_POLICIES:
        return "PURCHASE_CONFIRMATION_POLICY_UNRECOGNIZED"
    if intent.get("schema_version") not in RECOGNIZED_POLICIES[str(policy)]:
        return "PURCHASE_CONFIRMATION_INTENT_SCHEMA_INVALID"
    if intent.get("manual_purchase_required") is not True:
        return "PURCHASE_CONFIRMATION_MANUAL_REQUIREMENT_MISSING"
    if intent.get("recommendation_status") != "MANUAL_BUY_RECOMMENDED":
        return "PURCHASE_CONFIRMATION_INTENT_NOT_RECOMMENDED"
    if _valid_positive_int(intent.get("recommended_stake_yen")) != 100:
        return "PURCHASE_CONFIRMATION_STAKE_CONTRACT_INVALID"
    first, second = _valid_positive_int(intent.get("pair_i")), _valid_positive_int(intent.get("pair_j"))
    if first is None or second is None or first == second:
        return "PURCHASE_CONFIRMATION_PAIR_INVALID"
    if not isinstance(intent.get("race_key"), str) or not intent["race_key"]:
        return "PURCHASE_CONFIRMATION_RACE_KEY_INVALID"
    if intent.get("reference_mode") != "T15_STANDARD":
        return "PURCHASE_CONFIRMATION_NON_STANDARD_REFERENCE"
    if intent.get("scientific_sample") is not True:
        return "PURCHASE_CONFIRMATION_NOT_SCIENTIFIC_SAMPLE"
    if not isinstance(intent.get("date"), str) or not isinstance(intent.get("venue"), str) or _valid_positive_int(intent.get("race_number")) is None:
        return "PURCHASE_CONFIRMATION_RACE_IDENTITY_INVALID"
    return None


def _deadline_from_existing_pre_race_evidence(intent: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Use immutable Main evidence only when it is locally present and exact."""
    if not EVIDENCE_DB.exists():
        return "USER_CONFIRMED_TIME_ONLY", None, None
    try:
        connection = sqlite3.connect(f"file:{EVIDENCE_DB}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """SELECT rr.bundle_path,rr.bundle_sha256,rr.reference_mode,r.race_key
                     FROM recommendation_records rr JOIN race_registry r ON r.race_key=rr.race_key
                    WHERE r.race_key=? AND r.race_date=? AND r.venue=? AND r.race_number=?""",
                (intent["race_key"], intent["date"], intent["venue"], int(intent["race_number"])),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return "USER_CONFIRMED_TIME_ONLY", None, None
        return "PRE_RACE_EVIDENCE_INVALID", None, type(exc).__name__
    except sqlite3.Error as exc:
        return "PRE_RACE_EVIDENCE_INVALID", None, type(exc).__name__
    if not rows:
        return "USER_CONFIRMED_TIME_ONLY", None, None
    if len(rows) != 1 or rows[0]["reference_mode"] != "T15_STANDARD":
        return "PRE_RACE_EVIDENCE_INVALID", None, "RECOMMENDATION_REFERENCE_CONTRACT"
    bundle_path = Path(str(rows[0]["bundle_path"]))
    bundle_path = bundle_path if bundle_path.is_absolute() else ROOT / bundle_path
    try:
        raw = bundle_path.read_bytes()
        bundle = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return "PRE_RACE_EVIDENCE_INVALID", None, "BUNDLE_UNAVAILABLE"
    if _sha(raw) != str(rows[0]["bundle_sha256"]) or not isinstance(bundle, dict):
        return "PRE_RACE_EVIDENCE_INVALID", None, "BUNDLE_HASH_MISMATCH"
    race, reference = bundle.get("race"), bundle.get("predecision_reference")
    if not isinstance(race, dict) or not isinstance(reference, dict) or reference.get("mode") != "T15_STANDARD":
        return "PRE_RACE_EVIDENCE_INVALID", None, "BUNDLE_REFERENCE_CONTRACT"
    try:
        bundle_race_number = int(race.get("race_number", -1))
    except (TypeError, ValueError):
        return "PRE_RACE_EVIDENCE_INVALID", None, "BUNDLE_RACE_IDENTITY"
    if race.get("race_key") != intent["race_key"] or race.get("race_date") != intent["date"] or race.get("venue") != intent["venue"] or bundle_race_number != int(intent["race_number"]):
        return "PRE_RACE_EVIDENCE_INVALID", None, "BUNDLE_RACE_IDENTITY"
    try:
        deadline = _iso(str(race["scheduled_post_time"]))
    except (KeyError, TypeError, ValueError):
        return "PRE_RACE_EVIDENCE_INVALID", None, "BUNDLE_DEADLINE_INVALID"
    return "PRE_RACE_CONFIRMED", deadline, None


def _confirmation_path(intent: dict[str, Any], intent_sha256: str) -> Path:
    policy = str(intent["policy_id"])
    return OUT / str(intent["date"]) / f"{intent['venue']}_race{int(intent['race_number']):02d}_{policy}_{intent_sha256[:16]}.json"


def _matching_prior_confirmation(*, date: str, intent_path: str, intent_path_resolved: str) -> tuple[dict[str, Any] | None, str | None]:
    directory = OUT / date
    if not directory.exists():
        return None, None
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, "PURCHASE_CONFIRMATION_EVIDENCE_CORRUPT"
        if not isinstance(value, dict):
            return None, "PURCHASE_CONFIRMATION_EVIDENCE_CORRUPT"
        if value.get("intent_path") == intent_path or value.get("intent_path_resolved") == intent_path_resolved:
            return value, None
    return None, None


def confirm_purchase(
    *, intent_path: Path, confirm_purchased: bool = False, confirm_not_purchased: bool = False,
    confirmed_at: datetime | None = None, confirmation_mode: str = "LIVE_EXPLICIT_USER",
    retroactive_manifest_reference: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Record only an explicit user confirmation; no purchase is performed."""
    if bool(confirm_purchased) == bool(confirm_not_purchased):
        return {"status": "PURCHASE_CONFIRMATION_EXPLICIT_FLAG_REQUIRED", "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    if confirmation_mode not in {"LIVE_EXPLICIT_USER", "RETROACTIVE_USER_CONFIRMED"}:
        return {"status": "PURCHASE_CONFIRMATION_MODE_INVALID", "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    if confirmation_mode == "RETROACTIVE_USER_CONFIRMED" and not retroactive_manifest_reference:
        return {"status": "PURCHASE_CONFIRMATION_RETROACTIVE_MANIFEST_REQUIRED", "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    confirmation_status = PURCHASED if confirm_purchased else NOT_PURCHASED
    try:
        path = _resolve_intent_path(intent_path)
    except ValueError as exc:
        return {"status": str(exc), "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    parsed = _read_intent(path)
    if parsed is None:
        return {"status": "PURCHASE_CONFIRMATION_INTENT_UNAVAILABLE", "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    intent, raw = parsed
    if (reason := _validate_intent(intent)) is not None:
        return {"status": reason, "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    try:
        path.relative_to(_policy_intent_root(str(intent["policy_id"])).resolve())
    except ValueError:
        return {"status": "PURCHASE_CONFIRMATION_INTENT_PATH_POLICY_MISMATCH", "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    if path.parent.name != str(intent["date"]):
        return {"status": "PURCHASE_CONFIRMATION_INTENT_PATH_DATE_MISMATCH", "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    now = _utc(confirmed_at or datetime.now(timezone.utc))
    display = _display_path(path)
    intent_sha256 = _sha(raw)
    prior, prior_error = _matching_prior_confirmation(date=str(intent["date"]), intent_path=display, intent_path_resolved=str(path))
    if prior_error is not None:
        return {"status": prior_error, "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    if prior is not None and prior.get("intent_sha256") != intent_sha256:
        return {"status": "PURCHASE_CONFIRMATION_INTENT_HASH_MISMATCH", "written": False, "intent_sha256": intent_sha256, "prior_intent_sha256": prior.get("intent_sha256"), "result_db_accessed": 0, "actual_bets_written": False}
    timing, deadline, timing_error = _deadline_from_existing_pre_race_evidence(intent)
    if timing == "PRE_RACE_EVIDENCE_INVALID":
        return {"status": "PURCHASE_CONFIRMATION_PRE_RACE_EVIDENCE_INVALID", "reason": timing_error, "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    if confirmation_mode == "LIVE_EXPLICIT_USER" and deadline is not None and now >= _utc(deadline):
        return {"status": "PURCHASE_CONFIRMATION_AFTER_PRE_RACE_DEADLINE", "authoritative_deadline": deadline, "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    output = _confirmation_path(intent, intent_sha256)
    evidence = {
        "component_id": COMPONENT_ID, "confirmation_version": CONFIRMATION_VERSION,
        "confirmation_status": confirmation_status, "confirmed_at": _iso(now),
        "intent_path": display, "intent_path_resolved": str(path), "intent_sha256": intent_sha256,
        "confirmation_id": COMPONENT_ID + "::" + _sha(_canonical({"intent_path": str(path), "intent_sha256": intent_sha256, "status": confirmation_status})),
        "policy_id": intent["policy_id"], "race_key": intent["race_key"], "date": intent["date"], "venue": intent["venue"], "race_number": int(intent["race_number"]),
        "pair_i": int(intent["pair_i"]), "pair_j": int(intent["pair_j"]),
        "recommended_stake_yen": 100, "actual_stake_yen": 100 if confirmation_status == PURCHASED else 0,
        "reference_mode": intent["reference_mode"], "recommendation_status": intent["recommendation_status"],
        "manual_user_confirmation": True, "manual_confirmation": True, "automatic_purchase": False, "actual_bets_written": False,
        "confirmation_timing": timing, "authoritative_deadline": deadline,
        "result_db_accessed": 0,
    }
    if confirmation_mode == "RETROACTIVE_USER_CONFIRMED":
        evidence["confirmation_mode"] = confirmation_mode
        evidence["retroactive_migration_manifest"] = dict(retroactive_manifest_reference or {})
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "PURCHASE_CONFIRMATION_CONFLICT", "path": _display_path(output), "written": False, "result_db_accessed": 0, "actual_bets_written": False}
        if not isinstance(existing, dict) or any(
            existing.get(key) != evidence[key]
            for key in evidence
            if key != "confirmed_at" and not (key == "manual_confirmation" and key not in existing)
        ):
            return {"status": "PURCHASE_CONFIRMATION_CONFLICT", "path": _display_path(output), "written": False, "result_db_accessed": 0, "actual_bets_written": False}
        return existing | {"status": "PURCHASE_CONFIRMATION_IDEMPOTENT", "path": _display_path(output), "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    if prior is not None:
        return {"status": "PURCHASE_CONFIRMATION_CONFLICT", "written": False, "result_db_accessed": 0, "actual_bets_written": False}
    _atomic_json(output, evidence)
    return evidence | {"status": confirmation_status, "path": _display_path(output), "written": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record an explicit manual purchase of one Experimental WIDE intent.")
    parser.add_argument("--intent", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--confirm-purchased", action="store_true")
    group.add_argument("--confirm-not-purchased", action="store_true")
    args = parser.parse_args()
    print(json.dumps(confirm_purchase(intent_path=args.intent, confirm_purchased=args.confirm_purchased, confirm_not_purchased=args.confirm_not_purchased), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
