"""Bounded administrative import for the three user-confirmed 2026-09-01 actions.

This is deliberately not a generic retroactive-purchase command.  The exact
facts below are the sole accepted source assertions for this one migration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.actual_purchase_accounting import (
    ACTUAL_ROOT,
    DEFAULT_OUTPUT_ROOT,
    NOT_PURCHASED,
    PURCHASED,
    ActualPurchaseAccountingError,
    _load_main_ticket,
    _read_json,
    _sha,
    _canonical,
    _immutable_create,
    confirm_main_purchase,
    evaluate_actual_day,
    rebuild_actual_cumulative,
)
from src.operations.live_development_store import DEFAULT_DB, ROOT, canonical_combination
from src.operations import wide_experimental_purchase_confirm as wide_confirmation


TASK_ID = "P2-ACTUAL-BET-RETROACTIVE-IMPORT-20260901-018"
DATE = "2026-09-01"
VENUE = "大井"
MODE = "RETROACTIVE_USER_CONFIRMED"
AUDIT_ROOT = ROOT / "audit" / "data" / "p2_actual_bet_retroactive_import_20260901"
MANIFEST_PATH = AUDIT_ROOT / "migration_manifest.json"
OHI_INTENT = ROOT / "outputs" / "live_development" / "wide_ohi_experimental_v0" / "intents" / DATE / "大井_race12_experimental.json"

MAIN_ASSERTIONS = (
    {"race_number": 11, "ticket_type": "WIN", "selections": [1], "actual_stake_yen": 100, "confirmation_status": PURCHASED},
    {"race_number": 12, "ticket_type": "WIN", "selections": [9], "actual_stake_yen": 100, "confirmation_status": PURCHASED},
)
WIDE_ASSERTION = {
    "race_number": 12, "ticket_type": "WIDE", "selections": [7, 12], "recommended_stake_yen": 100,
    "actual_stake_yen": 0, "confirmation_status": NOT_PURCHASED,
    "reason": "ENGINEERING_FALSE_SUSPENSION_AT_DECISION_TIME",
}


class RetroactiveImportError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RetroactiveImportError("RETROACTIVE_IMPORT_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _resolve_main(*, assertion: dict[str, Any], evidence_db: Path) -> dict[str, Any]:
    # Resolve by natural race first, then only accept the exact expected ticket.
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{evidence_db.resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT rr.recommendation_id,rt.ticket_index
                     FROM recommendation_records rr JOIN race_registry r ON r.race_key=rr.race_key
                     JOIN recommendation_tickets rt ON rt.recommendation_id=rr.recommendation_id
                    WHERE r.race_date=? AND r.venue=? AND r.race_number=?
                      AND rr.decision_status='BET' AND rt.ticket_type=? AND rt.selections_json=? AND rt.stake_yen=?""",
                (DATE, VENUE, int(assertion["race_number"]), assertion["ticket_type"], json.dumps(assertion["selections"], separators=(",", ":")), int(assertion["actual_stake_yen"])),
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_RECOMMENDATION_LINKAGE", type(exc).__name__) from exc
    if len(rows) != 1:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_RECOMMENDATION_LINKAGE", f"race{assertion['race_number']}:matches={len(rows)}")
    ticket = _load_main_ticket(recommendation_id=str(rows[0]["recommendation_id"]), ticket_index=int(rows[0]["ticket_index"]), evidence_db=evidence_db)
    expected_key = f"P2_RACE_V1::{DATE}\x1f{VENUE}\x1f{int(assertion['race_number'])}"
    if (ticket["race_key"], ticket["ticket_type"], ticket["selections"], ticket["recommended_stake_yen"]) != (expected_key, assertion["ticket_type"], assertion["selections"], assertion["actual_stake_yen"]):
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_RECOMMENDATION_LINKAGE", f"race{assertion['race_number']}:resolved-content")
    return ticket


def _resolve_wide() -> tuple[dict[str, Any], bytes, str]:
    parsed = wide_confirmation._read_intent(OHI_INTENT)
    if parsed is None:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_WIDE_CONFLICT", "intent-unavailable")
    intent, raw = parsed
    if wide_confirmation._validate_intent(intent) is not None:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_WIDE_CONFLICT", "intent-invalid")
    expected_key = f"P2_RACE_V1::{DATE}\x1f{VENUE}\x1f12"
    if (intent.get("policy_id"), intent.get("race_key"), intent.get("date"), intent.get("venue"), int(intent.get("race_number", -1)),
            int(intent.get("pair_i", -1)), int(intent.get("pair_j", -1)), int(intent.get("recommended_stake_yen", -1))) != (
            "P2_WIDE_OHI_EXPERIMENTAL_V0", expected_key, DATE, VENUE, 12, 7, 12, 100):
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_WIDE_CONFLICT", "intent-content")
    digest = _sha(raw)
    prior, error = wide_confirmation._matching_prior_confirmation(date=DATE, intent_path=_relative(OHI_INTENT), intent_path_resolved=str(OHI_INTENT.resolve()))
    if error is not None:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_WIDE_CONFLICT", error)
    if prior is not None and prior.get("confirmation_status") == PURCHASED:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_WIDE_CONFLICT", "purchased-confirmation-exists")
    if prior is not None and (prior.get("confirmation_status") != NOT_PURCHASED or prior.get("intent_sha256") != digest):
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_WIDE_CONFLICT", "nonmatching-confirmation-exists")
    return intent, raw, digest


def _manifest_body(*, main: list[dict[str, Any]], wide_intent_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": "p2_actual_bet_retroactive_import_20260901_v1",
        "task_id": TASK_ID, "confirmation_mode": MODE,
        "actions": [
            {"source_type": "MAIN_RECOMMENDATION", "assertion": assertion,
             "repository_linkage": {key: ticket[key] for key in ("race_key", "recommendation_id", "recommendation_payload_sha256", "ticket_index", "policy_id", "ticket_type", "selections", "normalized_combination_key", "recommended_stake_yen")}}
            for assertion, ticket in zip(MAIN_ASSERTIONS, main, strict=True)
        ] + [
            {"source_type": "WIDE_EXPERIMENTAL", "assertion": WIDE_ASSERTION,
             "repository_linkage": {"intent_path": _relative(OHI_INTENT), "intent_sha256": wide_intent_sha256,
                                    "race_key": f"P2_RACE_V1::{DATE}\x1f{VENUE}\x1f12", "policy_id": "P2_WIDE_OHI_EXPERIMENTAL_V0",
                                    "ticket_type": "WIDE", "selections": [7, 12], "normalized_combination_key": canonical_combination("WIDE", "7-12"), "recommended_stake_yen": 100}}
        ],
    }


def _manifest(*, body: dict[str, Any], now: datetime) -> tuple[dict[str, Any], str]:
    digest = _sha(_canonical(body))
    if MANIFEST_PATH.exists():
        existing, _ = _read_json(MANIFEST_PATH, "RETROACTIVE_IMPORT_MANIFEST_CORRUPT")
        if existing.get("canonical_payload_sha256") != digest or existing.get("canonical_payload") != body:
            raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_MANIFEST_CONFLICT")
        return existing, _sha(MANIFEST_PATH.read_bytes())
    value = {"canonical_payload": body, "canonical_payload_sha256": digest, "created_at": _iso(now)}
    if not _immutable_create(MANIFEST_PATH, value):
        return _manifest(body=body, now=now)
    return value, _sha(MANIFEST_PATH.read_bytes())


def run(*, confirmed_at: str | datetime | None = None, evidence_db: Path = DEFAULT_DB) -> dict[str, Any]:
    now = _utc(confirmed_at)
    main = [_resolve_main(assertion=assertion, evidence_db=evidence_db) for assertion in MAIN_ASSERTIONS]
    _intent, _raw, intent_sha = _resolve_wide()
    manifest, manifest_sha = _manifest(body=_manifest_body(main=main, wide_intent_sha256=intent_sha), now=now)
    reference = {"path": _relative(MANIFEST_PATH), "sha256": manifest_sha, "task_id": TASK_ID}
    main_actions = []
    try:
        for ticket, assertion in zip(main, MAIN_ASSERTIONS, strict=True):
            main_actions.append(confirm_main_purchase(
                recommendation_id=ticket["recommendation_id"], ticket_index=ticket["ticket_index"],
                confirmation_status=PURCHASED, stake_yen=100, confirmed_at=now, evidence_db=evidence_db,
                confirmation_mode=MODE, retroactive_manifest_reference=reference,
            ))
    except ActualPurchaseAccountingError as exc:
        raise RetroactiveImportError(exc.code, exc.detail) from exc
    wide = wide_confirmation.confirm_purchase(
        intent_path=OHI_INTENT, confirm_not_purchased=True, confirmed_at=now,
        confirmation_mode=MODE, retroactive_manifest_reference=reference,
    )
    if wide.get("status") not in {NOT_PURCHASED, "PURCHASE_CONFIRMATION_IDEMPOTENT"}:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_WIDE_CONFLICT", str(wide.get("status")))
    daily = evaluate_actual_day(date=DATE, venue=VENUE, races=[11, 12], evidence_db=evidence_db, settlement_db=evidence_db)
    if daily.get("accounting_status") != "COMPLETE":
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_ACTUAL_ACCOUNTING", str(daily.get("accounting_status")))
    expected = (200, 0, -200, -1.0, 0.0)
    observed = tuple(daily.get(field) for field in ("turnover_yen", "gross_payout_yen", "net_profit_yen", "net_roi", "recovery_rate"))
    if observed != expected:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_OFFICIAL_CASH_DISCREPANCY", json.dumps({"expected": expected, "observed": observed}))
    cumulative = rebuild_actual_cumulative(through_date=DATE)
    if DATE in cumulative.get("coverage_gap_dates", []):
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_CUMULATIVE_COVERAGE_GAP")
    monetary = sum(len((row.get("settlement") or {}).get("tickets", [])) for row in daily.get("race_rows", []))
    if monetary != 2:
        raise RetroactiveImportError("BLOCKED_ON_RETROACTIVE_IMPORT_MONETARY_TICKET_COUNT", str(monetary))
    return {"status": "P2_ACTUAL_BET_RETROACTIVE_IMPORT_20260901_VERIFIED", "migration_manifest": manifest,
            "migration_manifest_sha256": manifest_sha, "main_actions": main_actions, "wide_action": wide,
            "daily": daily, "cumulative": cumulative, "monetary_ticket_count": monetary,
            "result_value_access": "ALLOWED_FOR_ACTUAL_SETTLEMENT_ONLY", "payout_value_access": "ALLOWED_FOR_ACTUAL_SETTLEMENT_ONLY"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Administrative bounded 2026-09-01 Actual Purchase import; no generic retroactive interface.")
    parser.add_argument("--confirmed-at", help="timezone-aware import execution timestamp; never a historical purchase time")
    parser.add_argument("--evidence-db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    try:
        value = run(confirmed_at=args.confirmed_at, evidence_db=args.evidence_db)
    except RetroactiveImportError as exc:
        print(json.dumps({"status": exc.code, "detail": exc.detail}, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
