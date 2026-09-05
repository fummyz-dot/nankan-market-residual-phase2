import hashlib
import json
from pathlib import Path

import pytest

from src.operations.live_development_store import connect, initialize_database, register_race, transaction
from src.operations.settlement_evaluation import SettlementEvaluationError, evaluate_day, settle_race


POST = "2026-08-24T08:00:00+00:00"
CAPTURED = "2026-08-24T09:00:00+00:00"


def _race_key(number: int) -> str:
    return f"P2_RACE_V1::2026-08-24\x1f船橋\x1f{number}"


def _seed_race(db: Path, number: int) -> str:
    key = _race_key(number)
    conn = connect(db)
    try:
        with transaction(conn):
            register_race(conn, {"race_key": key, "race_date": "2026-08-24", "venue": "船橋", "race_number": number,
                                 "scheduled_post_time": POST, "source_entry_url": None})
    finally:
        conn.close()
    return key


def _seed_result(db: Path, temp: Path, key: str, *, winner: int = 1, payouts: dict[tuple[str, str], int] | None = None,
                 raw: bytes = b"<html><body>official final</body></html>", capture_id: str | None = None, captured_at: str = CAPTURED) -> None:
    capture_id = capture_id or f"capture-{key.rsplit(chr(31), 1)[-1]}"
    path = temp / f"{capture_id}.html"; path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    conn = connect(db)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (capture_id, key, "official://result", captured_at, 200, "text/html", str(path), digest, len(raw), "RESULT_OFFICIAL_FINAL", "test", "PARSED", captured_at))
            conn.execute("INSERT INTO official_runner_results VALUES(?,?,?,?,?,?,?)", (capture_id, key, winner, 1, "STARTER_VALID_FINISH", None, "PARSED"))
            for index, ((ticket_type, canonical), amount) in enumerate((payouts or {}).items(), start=1):
                conn.execute("INSERT INTO official_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f"payout-{capture_id}-{index}", capture_id, key, ticket_type, canonical, canonical, str(amount), amount, None, index, "PARSED"))
    finally:
        conn.close()


def _bundle(temp: Path, number: int, *, recommendation: dict, fallback: bool = False,
            candidate_one: float = 0.6, market_one: float = 0.5) -> tuple[Path, str]:
    path = temp / f"bundle-{number}.json"
    payload = {
        "recommendation": recommendation,
        "dev_live_v1": {"model": {"version": "DEV-LIVE-V1", "model_sha256": "model"}, "candidate": [
            {"horse_number": 1, "candidate_probability": candidate_one}, *[{"horse_number": item, "candidate_probability": (1.0 - candidate_one) / 4} for item in range(2, 6)],
        ]},
        "market": [{"horse_number": 1, "market_calibrated_probability": market_one}, *[{"horse_number": item, "market_calibrated_probability": (1.0 - market_one) / 4} for item in range(2, 6)]],
        "predecision_reference": {"mode": "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD"},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(); path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _seed_evidence(db: Path, temp: Path, key: str, number: int, *, tickets: list[dict], status: str = "BET", fallback: bool = False,
                   candidate_one: float = 0.6, market_one: float = 0.5) -> None:
    bundle_tickets = [{**item, "recommended": True} for item in tickets]
    recommendation = {"decision_status": status, "policy_id": "P2_OPS_BET_POLICY_V1", "tickets": bundle_tickets, "total_stake_yen": sum(item.get("stake_yen", 0) for item in tickets)}
    path, digest = _bundle(temp, number, recommendation=recommendation, fallback=fallback, candidate_one=candidate_one, market_one=market_one)
    conn = connect(db)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO recommendation_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                f"rec-{number}", key, CAPTURED, str(path), digest, f"payload-{number}", "DEV-LIVE-V1", "model", "P2_OPS_BET_POLICY_V1", "policy",
                "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD", "RECOVERY" if fallback else "T15", CAPTURED, 600.0,
                status, "FULL", recommendation["total_stake_yen"], json.dumps(recommendation, ensure_ascii=False, sort_keys=True),
            ))
            for index, ticket in enumerate(tickets, start=1):
                selections = sorted(ticket["selections"]) if ticket["ticket_type"] == "WIDE" else ticket["selections"]
                conn.execute("INSERT INTO recommendation_tickets VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    f"rec-{number}", index, ticket["ticket_type"], json.dumps(selections, separators=(",", ":")), ticket["stake_yen"], 0.1, 0.1, 1.0, 1.0, 1.0,
                ))
    finally:
        conn.close()


def _seed_legacy(db: Path, key: str, number: int, *, tickets: list[dict], status: str = "BET") -> None:
    decision_id = f"decision-{number}"
    conn = connect(db)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO decision_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                decision_id, key, 1, "FROZEN", status, "2026-08-24T07:30:00+00:00", "2026-08-24T07:45:00+00:00", f"decision-hash-{number}",
                "market", "current", "missing-legacy-bundle.json", "missing", "DEV-LIVE-V1", "FS04", "model", 0, CAPTURED,
            ))
            for horse, candidate, market in ((1, 0.6, 0.5), (2, 0.4, 0.5)):
                conn.execute("INSERT INTO decision_runner_predictions VALUES(?,?,?,?,?,?)", (decision_id, horse, candidate, market, 0.0, horse))
            for index, ticket in enumerate(tickets, start=1):
                conn.execute("INSERT INTO decision_tickets VALUES(?,?,?,?,?,?,?)", (f"legacy-ticket-{number}-{index}", decision_id, ticket["ticket_type"], json.dumps(ticket["selections"]), ticket["stake_units"], None, "[]"))
    finally:
        conn.close()


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "live.sqlite"; initialize_database(path); return path


def test_evidence_win_and_wide_hit_and_fallback_split(db: Path, tmp_path: Path) -> None:
    key = _seed_race(db, 1)
    tickets = [{"ticket_type": "WIN", "selections": [1], "stake_yen": 100}, {"ticket_type": "WIDE", "selections": [1, 2], "stake_yen": 100}]
    _seed_evidence(db, tmp_path, key, 1, tickets=tickets, fallback=True)
    _seed_result(db, tmp_path, key, payouts={("WIN", "1"): 250, ("WIDE", "1-2"): 650})
    row = settle_race(race_key=key, db_path=db)
    assert row["settlement_status"] == "SETTLED"
    assert row["gross_return_yen"] == 900 and row["pnl_yen"] == 700
    assert [item["settlement_status"] for item in row["tickets"]] == ["HIT", "HIT"]
    assert row["win_evaluation"]["reference_bucket"] == "FALLBACK"


def test_win_and_wide_miss_and_idempotency(db: Path, tmp_path: Path) -> None:
    key = _seed_race(db, 2)
    _seed_evidence(db, tmp_path, key, 2, tickets=[{"ticket_type": "WIN", "selections": [2], "stake_yen": 100}, {"ticket_type": "WIDE", "selections": [1, 2], "stake_yen": 100}])
    _seed_result(db, tmp_path, key, payouts={("WIN", "1"): 200, ("WIDE", "1-3"): 400})
    assert [item["settlement_status"] for item in settle_race(race_key=key, db_path=db)["tickets"]] == ["MISS", "MISS"]
    assert settle_race(race_key=key, db_path=db)["status"] == "IDEMPOTENT_NOOP"


def test_no_bet_and_no_pre_race_recommendation_are_distinct(db: Path, tmp_path: Path) -> None:
    no_bet = _seed_race(db, 3); no_recommendation = _seed_race(db, 4)
    _seed_evidence(db, tmp_path, no_bet, 3, tickets=[], status="NO_BET")
    _seed_result(db, tmp_path, no_bet); _seed_result(db, tmp_path, no_recommendation)
    assert settle_race(race_key=no_bet, db_path=db)["settlement_status"] == "NO_BET_SETTLED"
    assert settle_race(race_key=no_recommendation, db_path=db)["settlement_status"] == "NO_PRE_RACE_RECOMMENDATION"


def test_legacy_stake_units_and_dual_source_rules(db: Path, tmp_path: Path) -> None:
    key = _seed_race(db, 5)
    _seed_legacy(db, key, 5, tickets=[{"ticket_type": "WIN", "selections": [1], "stake_units": 2}])
    _seed_result(db, tmp_path, key, payouts={("WIN", "1"): 200})
    row = settle_race(race_key=key, db_path=db)
    assert row["strategy"]["strategy_source"] == "LEGACY_FROZEN_DECISION" and row["total_stake_yen"] == 200
    conflict_key = _seed_race(db, 6)
    _seed_legacy(db, conflict_key, 6, tickets=[{"ticket_type": "WIN", "selections": [1], "stake_units": 1}])
    _seed_evidence(db, tmp_path, conflict_key, 6, tickets=[{"ticket_type": "WIN", "selections": [2], "stake_yen": 100}])
    _seed_result(db, tmp_path, conflict_key, payouts={("WIN", "1"): 200})
    with pytest.raises(SettlementEvaluationError, match="DUAL_STRATEGY_SOURCE_CONFLICT"):
        settle_race(race_key=conflict_key, db_path=db)


def test_refund_and_payout_incomplete_fail_closed(db: Path, tmp_path: Path) -> None:
    refund_key = _seed_race(db, 7)
    _seed_evidence(db, tmp_path, refund_key, 7, tickets=[{"ticket_type": "WIN", "selections": [1], "stake_yen": 100}])
    raw = b"<div class='pc'><table><tr><th>\xe5\x82\x99\xe8\x80\x83</th></tr><tr><td>\xe8\xbf\x94\xe9\x82\x84\xef\xbc\x9a1\xe5\x8f\xb7\xe9\xa6\xac</td></tr></table></div>"
    _seed_result(db, tmp_path, refund_key, payouts={("WIN", "2"): 300}, raw=raw)
    assert settle_race(race_key=refund_key, db_path=db)["tickets"][0]["settlement_status"] == "REFUND"
    incomplete_key = _seed_race(db, 8)
    _seed_evidence(db, tmp_path, incomplete_key, 8, tickets=[{"ticket_type": "WIDE", "selections": [1, 2], "stake_yen": 100}])
    _seed_result(db, tmp_path, incomplete_key, payouts={("WIN", "1"): 200})
    with pytest.raises(SettlementEvaluationError, match="PAYOUT_INCOMPLETE"):
        settle_race(race_key=incomplete_key, db_path=db)


def test_daily_report_and_actual_bets_boundary(db: Path, tmp_path: Path) -> None:
    key = _seed_race(db, 9)
    tickets = [{"ticket_type": "WIN", "selections": [1], "stake_yen": 100}]
    _seed_evidence(db, tmp_path, key, 9, tickets=tickets)
    _seed_result(db, tmp_path, key, payouts={("WIN", "1"): 100})
    report = evaluate_day(date="2026-08-24", venue="船橋", races=[9], db_path=db, output_root=tmp_path / "out")
    assert report["summary"]["coverage"]["BET"] == 1
    assert report["actual_bets_accessed"] == 0
    assert Path(report["report_path"]).is_file() and Path(report["manifest_path"]).is_file()


def test_ten_ticket_settlement_and_official_source_change_blocks(db: Path, tmp_path: Path) -> None:
    key = _seed_race(db, 10)
    tickets = [{"ticket_type": "WIDE", "selections": [left, right], "stake_yen": 100}
               for left in range(1, 5) for right in range(left + 1, 6)]
    assert len(tickets) == 10
    _seed_evidence(db, tmp_path, key, 10, tickets=tickets)
    _seed_result(db, tmp_path, key, payouts={("WIDE", "1-2"): 300})
    first = settle_race(race_key=key, db_path=db)
    assert len(first["tickets"]) == 10 and first["total_stake_yen"] == 1000
    _seed_result(db, tmp_path, key, payouts={("WIDE", "1-2"): 300}, raw=b"<html>official correction</html>", capture_id="capture-2", captured_at="2026-08-24T10:00:00+00:00")
    with pytest.raises(SettlementEvaluationError, match="OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"):
        settle_race(race_key=key, db_path=db)


def test_candidate_vs_market_log_loss_can_favor_market(db: Path, tmp_path: Path) -> None:
    key = _seed_race(db, 11)
    _seed_evidence(db, tmp_path, key, 11, tickets=[], status="NO_BET", candidate_one=0.4, market_one=0.6)
    _seed_result(db, tmp_path, key)
    assert settle_race(race_key=key, db_path=db)["win_evaluation"]["delta_ll"] > 0
