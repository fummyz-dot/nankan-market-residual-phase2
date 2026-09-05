"""Shared T15-standard / pre-race-fallback reference selection.

The module owns only capture/reference selection, bounded recovery coordination,
and provenance.  It never opens a result/outcome/decision ledger, scores a
model, derives features, or interprets race results.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = ROOT / "configs" / "pre_race_capture_policy_v1.json"
DEFAULT_LOCK_ROOT = ROOT / "outputs" / "pre_race_recovery_locks"


class PreRaceReferenceError(RuntimeError):
    pass


class RecoveryTransientError(PreRaceReferenceError):
    """A bounded retry is permitted under the fixed recovery policy."""


class RecoveryInvariantError(PreRaceReferenceError):
    """A source/identity/roster/DB invariant failed and must not retry."""


def utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PreRaceReferenceError("PRE_RACE_CAPTURE_NAIVE_TIMESTAMP_PROHIBITED")
    return parsed.astimezone(timezone.utc)


def iso(value: str | datetime) -> str:
    return utc(value).isoformat()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_capture_policy(path: Path = DEFAULT_POLICY_PATH) -> tuple[dict[str, Any], str]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1",
        "version": "1.0.0",
        "standard_reference": "T15",
        "prefer_standard_t15": True,
        "fallback_enabled": True,
        "hard_min_seconds_to_post": 120,
        "max_fallback_snapshot_age_seconds": 900,
        "fallback_retry_interval_seconds": 30,
        "fallback_max_attempts": 3,
    }
    if any(policy.get(key) != value for key, value in expected.items()):
        raise PreRaceReferenceError("PRE_RACE_CAPTURE_POLICY_CONTRACT_INVALID")
    return policy, sha256_file(path)


def seconds_to_post(*, scheduled_post_time: str | datetime, now: datetime) -> float:
    return (utc(scheduled_post_time) - utc(now)).total_seconds()


def _notes(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise PreRaceReferenceError("PRE_RACE_CAPTURE_SNAPSHOT_NOTES_INVALID") from exc
    if not isinstance(parsed, dict):
        raise PreRaceReferenceError("PRE_RACE_CAPTURE_SNAPSHOT_NOTES_INVALID")
    return parsed


def _scheduled_post_drift_fallback(snapshot: dict[str, Any]) -> bool:
    """Allow only the collector's explicit timing-drift T15 fallback path."""
    return (
        str(snapshot.get("snapshot_mark")) == "T15"
        and _notes(snapshot.get("notes")).get("fallback_reason") == "SCHEDULED_POST_TIME_DRIFT"
    )


def _adapt_wide(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adapted: list[dict[str, Any]] = []
    for row in rows:
        parts = str(row.get("normalized_combination_key") or "").split("-")
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            adapted.append(row | {"horse_number_1": int(parts[0]), "horse_number_2": int(parts[1])})
        else:
            # Retain the malformed raw key for the existing WIDE fail-closed
            # validator; WIN reference selection remains valid.
            adapted.append(row | {"horse_number_1": None, "horse_number_2": None})
    return adapted


def _adapt_trio(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adapted: list[dict[str, Any]] = []
    for row in rows:
        parts = str(row.get("normalized_combination_key") or "").split("-")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            adapted.append(row | {
                "horse_number_1": int(parts[0]), "horse_number_2": int(parts[1]), "horse_number_3": int(parts[2]),
            })
        else:
            adapted.append(row | {"horse_number_1": None, "horse_number_2": None, "horse_number_3": None})
    return adapted


def _valid_snapshot(
    conn: Any, *, race: dict[str, Any], snapshot: dict[str, Any], now: datetime,
    policy: dict[str, Any], require_standard_t15: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one retained CURRENT/WIN/WIDE capture set without outcomes."""
    mark = str(snapshot["snapshot_mark"])
    if mark == "T15" and require_standard_t15:
        if snapshot.get("t15_timing_status") != "PREDECISION_VALID":
            return None, "T15_NOT_PREDECISION_VALID"
    if snapshot.get("capture_status") != "COMPLETE":
        return None, "CURRENT_CAPTURE_NOT_COMPLETE"
    try:
        current_at, post = utc(snapshot["captured_at"]), utc(snapshot["scheduled_post_time"])
    except PreRaceReferenceError as exc:
        return None, str(exc)
    if current_at >= post:
        return None, "CURRENT_CAPTURE_NOT_PRE_RACE"
    age = (utc(now) - current_at).total_seconds()
    if not require_standard_t15 and (age < 0 or age > int(policy["max_fallback_snapshot_age_seconds"])):
        return None, "FALLBACK_SNAPSHOT_AGE_INVALID"
    current = [dict(row) for row in conn.execute(
        "SELECT * FROM current_runner_info WHERE current_snapshot_id=? ORDER BY horse_number",
        (snapshot["current_snapshot_id"],),
    )]
    active = {int(row["horse_number"]) for row in current}
    if not active or len(active) != len(current) or len(current) != int(snapshot.get("active_runner_count") or -1):
        return None, "CURRENT_ACTIVE_ROSTER_INVALID"
    raw = conn.execute("SELECT raw_archive_path FROM source_captures WHERE capture_id=?", (snapshot["raw_capture_id"],)).fetchall()
    if len(raw) != 1 or not raw[0][0]:
        return None, "CURRENT_RAW_PROVENANCE_MISSING"
    notes = _notes(snapshot.get("notes"))
    win_capture_id = notes.get("market_win_capture_id") or notes.get("market_capture_id")
    if not win_capture_id:
        return None, "WIN_CAPTURE_SET_MISSING"
    win = [dict(row) for row in conn.execute(
        """SELECT snapshot_id,capture_id,CAST(normalized_combination_key AS INTEGER) AS horse_number,
                  odds_value,captured_at,scheduled_post_time,field_size
             FROM market_snapshots
            WHERE race_registry_id=? AND bet_type_code='WIN' AND capture_id=?
            ORDER BY horse_number""",
        (race["race_registry_id"], str(win_capture_id)),
    )]
    win_numbers = [int(row["horse_number"]) for row in win]
    if set(win_numbers) != active or len(set(win_numbers)) != len(win_numbers):
        return None, "WIN_ACTIVE_ROSTER_MISMATCH"
    try:
        market_at = {iso(row["captured_at"]) for row in win}
        market_post = {iso(row["scheduled_post_time"]) for row in win}
    except PreRaceReferenceError as exc:
        return None, str(exc)
    if len(market_at) != 1 or len(market_post) != 1 or utc(next(iter(market_at))) >= post:
        return None, "WIN_CAPTURE_TIMING_INVALID"
    if any(row["odds_value"] is None or not math.isfinite(float(row["odds_value"])) or float(row["odds_value"]) <= 0 for row in win):
        return None, "WIN_ODDS_INVALID"
    if any(row["field_size"] is None or int(row["field_size"]) != len(active) for row in win):
        return None, "WIN_FIELD_SIZE_INVALID"
    wide_capture_id = notes.get("market_wide_capture_id")
    wide_rows: list[dict[str, Any]] | None = None
    wide_status = notes.get("market_wide_status") or "WIDE_MARKET_INCOMPLETE"
    if wide_capture_id:
        wide_rows = _adapt_wide([dict(row) for row in conn.execute(
            """SELECT snapshot_id,capture_id,normalized_combination_key,odds_value AS lower_odds,
                      max_odds_value AS upper_odds,captured_at,scheduled_post_time,field_size,response_sha256,notes
                 FROM market_snapshots
                WHERE race_registry_id=? AND bet_type_code='WIDE' AND capture_id=?
                ORDER BY normalized_combination_key""",
            (race["race_registry_id"], str(wide_capture_id)),
        )])
        try:
            wide_pre_post = bool(wide_rows) and all(utc(row["captured_at"]) < post for row in wide_rows)
        except PreRaceReferenceError:
            wide_pre_post = False
        if not wide_pre_post:
            # A WIDE-only stale/after-post capture cannot enter a pre-race
            # policy.  Preserve valid WIN/CURRENT as a partial reference.
            wide_rows = None
            wide_status = "WIDE_MARKET_INCOMPLETE"
    trio_capture_id = notes.get("market_trio_capture_id")
    trio_rows: list[dict[str, Any]] | None = None
    trio_status = notes.get("market_trio_status") or "TRIO_MARKET_INCOMPLETE"
    if trio_capture_id:
        trio_rows = _adapt_trio([dict(row) for row in conn.execute(
            """SELECT snapshot_id,capture_id,normalized_combination_key,odds_value,captured_at,scheduled_post_time,
                      field_size,response_sha256,notes
                 FROM market_snapshots
                WHERE race_registry_id=? AND bet_type_code='TRIO' AND capture_id=?
                ORDER BY normalized_combination_key""",
            (race["race_registry_id"], str(trio_capture_id)),
        )])
        try:
            trio_pre_post = bool(trio_rows) and all(utc(row["captured_at"]) < post for row in trio_rows)
        except PreRaceReferenceError:
            trio_pre_post = False
        if not trio_pre_post:
            trio_rows = None
            trio_status = "TRIO_MARKET_INCOMPLETE"
    source_mark = mark
    timing_drift_fallback = _scheduled_post_drift_fallback(snapshot)
    mode = "T15_STANDARD" if mark == "T15" and snapshot.get("t15_timing_status") == "PREDECISION_VALID" and not timing_drift_fallback else "PRE_RACE_FALLBACK"
    standard_t15_status = "PREDECISION_VALID" if mode == "T15_STANDARD" else "MISSED"
    source_hashes = {
        "current_snapshot_sha256": str(snapshot["response_sha256"]),
        "market_snapshot_sha256": None,
        "wide_snapshot_sha256": None,
        "trio_snapshot_sha256": None,
    }
    if wide_capture_id:
        raw_hashes = conn.execute(
            "SELECT raw_sha256 FROM source_captures WHERE capture_id=?", (str(wide_capture_id),)
        ).fetchall()
        if len(raw_hashes) == 1 and raw_hashes[0][0]:
            source_hashes["wide_snapshot_sha256"] = str(raw_hashes[0][0])
    if trio_capture_id:
        raw_hashes = conn.execute(
            "SELECT raw_sha256 FROM source_captures WHERE capture_id=?", (str(trio_capture_id),)
        ).fetchall()
        if len(raw_hashes) == 1 and raw_hashes[0][0]:
            source_hashes["trio_snapshot_sha256"] = str(raw_hashes[0][0])
    raw_hashes = conn.execute(
        "SELECT raw_sha256 FROM source_captures WHERE capture_id=?", (str(win_capture_id),)
    ).fetchall()
    if len(raw_hashes) == 1 and raw_hashes[0][0]:
        source_hashes["market_snapshot_sha256"] = str(raw_hashes[0][0])
    return {
        "race": race,
        "snapshot": snapshot,
        "current_rows": current,
        "t15_win_rows": win,
        "t15_wide_rows": wide_rows,
        "t15_trio_rows": trio_rows,
        "raw_card_path": str(raw[0][0]),
        "reference": {
            "policy_id": policy["policy_id"],
            "mode": mode,
            "source_mark": source_mark,
            "market_capture_id": str(win_capture_id),
            "current_capture_id": str(snapshot["capture_id"]),
            "current_snapshot_id": str(snapshot["current_snapshot_id"]),
            "market_captured_at": next(iter(market_at)),
            "current_captured_at": iso(current_at),
            "scheduled_post_time": iso(post),
            "seconds_to_post_at_reference": seconds_to_post(scheduled_post_time=post, now=current_at),
            "snapshot_age_seconds_at_bundle": age,
            "standard_t15_status": standard_t15_status,
            "scientific_sample": mode == "T15_STANDARD",
            "fallback_reason": notes.get("fallback_reason") if mode == "PRE_RACE_FALLBACK" else None,
            "captured_mark": notes.get("captured_mark") or source_mark,
            "wide_capture_id": wide_capture_id,
            "wide_capture_status": wide_status,
            "trio_capture_id": trio_capture_id,
            "trio_capture_status": trio_status,
            "capture_set_rule": notes.get("market_capture_set_rule") or "EXACT_RETAINED_CURRENT_SNAPSHOT_CAPTURE_SET",
            "market_snapshot_id": str(wide_capture_id) if wide_capture_id else None,
            **source_hashes,
        },
        "t15_wide_snapshot_provenance": {
            "current_snapshot_id": snapshot["current_snapshot_id"],
            "win_capture_id": str(win_capture_id),
            "wide_capture_id": wide_capture_id,
            "selection_rule": "EXACT_CURRENT_SNAPSHOT_CAPTURE_SET_NOT_LATEST",
            "status": wide_status,
        },
        "t15_trio_snapshot_provenance": {
            "current_snapshot_id": snapshot["current_snapshot_id"],
            "win_capture_id": str(win_capture_id),
            "trio_capture_id": trio_capture_id,
            "selection_rule": "EXACT_CURRENT_SNAPSHOT_CAPTURE_SET_NOT_LATEST",
            "status": trio_status,
        },
    }, None


def select_pre_race_reference(
    *, db_path: Path, race_date: str, venue: str, race_number: int,
    now: datetime | None = None, policy_path: Path = DEFAULT_POLICY_PATH,
) -> dict[str, Any]:
    """Choose T15 or newest valid pre-race fallback from existing storage."""
    import sqlite3

    policy, policy_hash = load_capture_policy(policy_path)
    current_now = utc(now or datetime.now(timezone.utc))
    if not db_path.exists():
        return {"status": "REFERENCE_MISSING", "reason": "MARKET_DB_MISSING", "policy": policy, "policy_sha256": policy_hash}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        races = con.execute(
            "SELECT * FROM race_registry WHERE race_date=? AND venue=? AND race_number=?",
            (race_date, venue, int(race_number)),
        ).fetchall()
        if len(races) != 1:
            return {"status": "REFERENCE_MISSING", "reason": f"RACE_REGISTRY_EXACT_MATCH:{len(races)}", "policy": policy, "policy_sha256": policy_hash}
        race = dict(races[0])
        snapshots = [dict(row) for row in con.execute(
            "SELECT * FROM current_info_snapshots WHERE race_registry_id=? ORDER BY captured_at DESC,current_snapshot_id DESC",
            (race["race_registry_id"],),
        )]
        t15 = [item for item in snapshots if item["snapshot_mark"] == "T15"]
        for snapshot in t15:
            candidate, reason = _valid_snapshot(con, race=race, snapshot=snapshot, now=current_now, policy=policy, require_standard_t15=True)
            if candidate is not None:
                candidate["status"] = "READY"
                candidate["policy_sha256"] = policy_hash
                return candidate
        standard_status = "INVALID" if t15 else "MISSED"
        fallback: list[dict[str, Any]] = []
        for snapshot in snapshots:
            if snapshot["snapshot_mark"] not in {"T20", "T10", "T05", "RECOVERY"} and not _scheduled_post_drift_fallback(snapshot):
                continue
            candidate, _ = _valid_snapshot(con, race=race, snapshot=snapshot, now=current_now, policy=policy, require_standard_t15=False)
            if candidate is not None:
                candidate["reference"]["standard_t15_status"] = standard_status
                fallback.append(candidate)
        if fallback:
            selected = max(fallback, key=lambda item: (utc(item["reference"]["current_captured_at"]), item["snapshot"]["current_snapshot_id"]))
            selected["status"] = "READY"
            selected["policy_sha256"] = policy_hash
            return selected
        return {
            "status": "REFERENCE_MISSING", "reason": "NO_VALID_T15_OR_FALLBACK", "race": race,
            "scheduled_post_time": iso(race["scheduled_post_time"]), "standard_t15_status": standard_status,
            "policy": policy, "policy_sha256": policy_hash,
        }
    finally:
        con.close()


@contextmanager
def recovery_lock(*, race_key: str, lock_root: Path = DEFAULT_LOCK_ROOT) -> Iterator[None]:
    lock_root.mkdir(parents=True, exist_ok=True)
    path = lock_root / f"{race_key}.lock"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def recover_pre_race_reference(
    *, db_path: Path, race_date: str, venue: str, race_number: int,
    scheduled_post_time: str, recovery_capture: Callable[[int], dict[str, Any]],
    now_fn: Callable[[], datetime] | None = None, sleep_fn: Callable[[float], None] = time.sleep,
    policy_path: Path = DEFAULT_POLICY_PATH, lock_root: Path = DEFAULT_LOCK_ROOT,
) -> dict[str, Any]:
    """Lock, recheck, and make at most the fixed number of recovery attempts."""
    policy, policy_hash = load_capture_policy(policy_path)
    clock = now_fn or (lambda: datetime.now(timezone.utc))
    first = select_pre_race_reference(db_path=db_path, race_date=race_date, venue=venue, race_number=race_number, now=clock(), policy_path=policy_path)
    if first.get("status") == "READY":
        return {"status": "REUSED", "reference": first, "attempts": 0, "policy_sha256": policy_hash}
    seconds = seconds_to_post(scheduled_post_time=scheduled_post_time, now=clock())
    if seconds < int(policy["hard_min_seconds_to_post"]):
        return {"status": "TOO_LATE", "seconds_to_post": seconds, "min_required": policy["hard_min_seconds_to_post"], "attempts": 0, "policy_sha256": policy_hash}
    race_key = f"{race_date}_{venue}_{int(race_number):02d}"
    with recovery_lock(race_key=race_key, lock_root=lock_root):
        rechecked = select_pre_race_reference(db_path=db_path, race_date=race_date, venue=venue, race_number=race_number, now=clock(), policy_path=policy_path)
        if rechecked.get("status") == "READY":
            return {"status": "REUSED_AFTER_LOCK", "reference": rechecked, "attempts": 0, "policy_sha256": policy_hash}
        attempts = 0
        transient_errors: list[str] = []
        while attempts < int(policy["fallback_max_attempts"]):
            seconds = seconds_to_post(scheduled_post_time=scheduled_post_time, now=clock())
            if seconds < int(policy["hard_min_seconds_to_post"]):
                return {"status": "TOO_LATE", "seconds_to_post": seconds, "min_required": policy["hard_min_seconds_to_post"], "attempts": attempts, "policy_sha256": policy_hash, "transient_errors": transient_errors}
            attempts += 1
            try:
                recovery_capture(attempts)
            except RecoveryTransientError as exc:
                transient_errors.append(f"{type(exc).__name__}:{exc}")
                if attempts < int(policy["fallback_max_attempts"]):
                    sleep_fn(float(policy["fallback_retry_interval_seconds"]))
                continue
            except Exception as exc:
                return {"status": "FAILED_INVARIANT", "attempts": attempts, "error": f"{type(exc).__name__}:{exc}", "policy_sha256": policy_hash}
            selected = select_pre_race_reference(db_path=db_path, race_date=race_date, venue=venue, race_number=race_number, now=clock(), policy_path=policy_path)
            if selected.get("status") == "READY":
                return {"status": "RECOVERED", "reference": selected, "attempts": attempts, "policy_sha256": policy_hash, "transient_errors": transient_errors}
            # An incomplete but potentially refreshable official response is
            # retried under the fixed policy.  It must not become a valid
            # snapshot, and a later attempt still has to pass the full shared
            # reference validator.
            transient_errors.append("RECOVERY_CAPTURE_DID_NOT_CREATE_VALID_REFERENCE")
            if attempts < int(policy["fallback_max_attempts"]):
                sleep_fn(float(policy["fallback_retry_interval_seconds"]))
                continue
            return {"status": "RECOVERY_EXHAUSTED", "attempts": attempts, "errors": transient_errors, "policy_sha256": policy_hash}
        return {"status": "RECOVERY_EXHAUSTED", "attempts": attempts, "errors": transient_errors, "policy_sha256": policy_hash}
