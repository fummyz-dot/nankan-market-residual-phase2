"""Synthetic, outcome-isolated tests for P2 Actual Purchase Accounting V1."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import actual_purchase_accounting as accounting
from src.operations import wide_experimental_purchase_confirm as wide_confirm
from src.operations.live_development_store import canonical_combination, connect, initialize_database, register_race, transaction
from src.operations.recommendation_evidence import EVIDENCE_COMPATIBLE_FREEZE_STATUS, canonical_json, commit_recommendation_evidence, sha256_bytes
from src.operations.wide_ops_v0 import POLICY_V2_PATH, load_policy


NOW = datetime(2099, 1, 1, 9, 30, tzinfo=timezone.utc)
RACE_KEY = "P2_RACE_V1::2099-01-01\x1f船橋\x1f5"


def _ticket(kind: str = "WIN", selections: list[int] | None = None, stake: int = 100) -> dict:
    return {"ticket_type": kind, "selections": selections or [1], "model_probability": 0.2, "market_mass": 0.1,
            "probability_ratio": 2.0, "reference_odds": 6.0, "gross_expected_return_at_snapshot": 1.2,
            "passes_probability_threshold": True, "passes_ratio_threshold": True, "passes_ger_threshold": True,
            "passes_thresholds": True, "recommended": True, "rejection_reasons": [], "stake_yen": stake}


def _commit_main(root: Path, *, tickets: list[dict] | None = None) -> tuple[Path, dict]:
    policy, policy_sha = load_policy(POLICY_V2_PATH)
    tickets = tickets if tickets is not None else [_ticket()]
    bundle = {
        "schema_version": "p2_live_shadow_analysis_bundle_v1", "mode": "LIVE_SHADOW",
        "race": {"race_key": RACE_KEY, "race_date": "2099-01-01", "venue": "船橋", "race_number": 5,
                 "scheduled_post_time": "2099-01-01T10:00:00+00:00"},
        "active_roster": [{"horse_number": n} for n in range(1, 13)],
        "dev_live_v1": {"model": {"version": "DEV-LIVE-V1", "model_sha256": "m" * 64}},
        "predecision_reference": {"policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1", "mode": "T15_STANDARD", "source_mark": "T15",
                                    "market_capture_id": "m", "current_capture_id": "c", "market_captured_at": "2099-01-01T09:15:00+00:00",
                                    "current_captured_at": "2099-01-01T09:15:00+00:00", "scheduled_post_time": "2099-01-01T10:00:00+00:00",
                                    "seconds_to_post_at_reference": 2700.0, "scientific_sample": True},
        "recommendation": {"schema_version": "p2_ops_recommendation_v1", "policy_id": policy["policy_id"], "policy_file_sha256": policy_sha,
                             "decision_status": "BET" if tickets else "NO_BET", "scope_status": "FULL", "evaluated_ticket_types": ["WIN"],
                             "unavailable_ticket_types": [], "tickets": tickets, "total_stake_yen": sum(item["stake_yen"] for item in tickets),
                             "all_ticket_evaluations": {"WIN": [], "WIDE": [{"ticket_type": "WIDE", "recommended": False, "stake_yen": 0,
                                                                                 "passes_thresholds": False, "rejection_reasons": ["HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY"]}]}, "enabled_ticket_types": ["WIN"],
                             "disabled_ticket_types": [{"ticket_type": "WIDE", "reason": "HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY"}]},
        "source_boundary": {"result_db_accessed": 0}, "prediction_info": {"freeze_status": EVIDENCE_COMPATIBLE_FREEZE_STATUS},
        "provenance": {"bundle_sha256": None},
    }
    bundle["provenance"]["bundle_sha256"] = sha256_bytes(canonical_json(bundle))
    path = root / "bundle.json"; path.write_bytes(canonical_json(bundle) + b"\n")
    db = root / "live.sqlite"
    return db, commit_recommendation_evidence(bundle_path=path, db_path=db, created_at="2099-01-01T09:16:00+00:00")


def _seed_official(db: Path, root: Path, *, race_key: str, race_number: int, payouts: dict[tuple[str, str], int], refund_html: bytes = b"<html></html>") -> None:
    raw_path = root / f"official-{race_number}.html"; raw_path.write_bytes(refund_html)
    digest = hashlib.sha256(refund_html).hexdigest(); capture_id = f"result-{race_number}"
    conn = connect(db)
    try:
        with transaction(conn):
            register_race(conn, {"race_key": race_key, "race_date": "2099-01-01", "venue": "船橋", "race_number": race_number,
                                 "scheduled_post_time": "2099-01-01T10:00:00+00:00", "source_entry_url": None})
            conn.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (capture_id, race_key, "official://test", "2099-01-01T11:00:00+00:00", 200, "text/html", str(raw_path), digest, len(refund_html), "RESULT_OFFICIAL_FINAL", "test", "PARSED", "2099-01-01T11:00:00+00:00"))
            for index, ((kind, combo), amount) in enumerate(payouts.items(), start=1):
                conn.execute("INSERT INTO official_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f"pay-{race_number}-{index}", capture_id, race_key, kind, combo, combo, str(amount), amount, None, index, "PARSED"))
    finally:
        conn.close()


class MainPurchaseEvidenceTest(unittest.TestCase):
    def test_purchased_not_purchased_deadline_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, rec = _commit_main(root); evidence = root / "actual"
            first = accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                     confirmation_status=accounting.PURCHASED, use_recommended_stake=True,
                                                     confirmed_at=NOW, evidence_db=db, output_root=evidence)
            self.assertEqual((first["status"], first["actual_stake_yen"]), ("PURCHASED", 100))
            self.assertEqual(accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                               confirmation_status=accounting.PURCHASED, use_recommended_stake=True,
                                                               confirmed_at=NOW.replace(minute=31), evidence_db=db, output_root=evidence)["status"], "IDEMPOTENT_NOOP")
            with self.assertRaisesRegex(accounting.ActualPurchaseAccountingError, "ALREADY_COMMITTED_DIFFERENT"):
                accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                 confirmation_status=accounting.NOT_PURCHASED, confirmed_at=NOW, evidence_db=db, output_root=evidence)
            with self.assertRaisesRegex(accounting.ActualPurchaseAccountingError, "AFTER_POST"):
                accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                 confirmation_status=accounting.PURCHASED, use_recommended_stake=True,
                                                 confirmed_at="2099-01-01T10:00:00+00:00", evidence_db=db, output_root=root / "late")

    def test_explicit_stake_and_not_purchased_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, rec = _commit_main(root)
            value = accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                     confirmation_status=accounting.PURCHASED, stake_yen=200,
                                                     execution_odds=4.2, placed_at="2099-01-01T09:20:00+00:00",
                                                     confirmed_at=NOW, evidence_db=db, output_root=root / "a")
            self.assertEqual(value["actual_stake_yen"], 200)
            with self.assertRaisesRegex(accounting.ActualPurchaseAccountingError, "STAKE_UNIT"):
                accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                 confirmation_status=accounting.PURCHASED, stake_yen=50, confirmed_at=NOW, evidence_db=db, output_root=root / "b")
            with self.assertRaisesRegex(accounting.ActualPurchaseAccountingError, "NOT_PURCHASED_OPTIONS"):
                accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                 confirmation_status=accounting.NOT_PURCHASED, stake_yen=100, confirmed_at=NOW, evidence_db=db, output_root=root / "c")
            not_purchased = accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                             confirmation_status=accounting.NOT_PURCHASED, confirmed_at=NOW,
                                                             evidence_db=db, output_root=root / "d")
            self.assertEqual((not_purchased["confirmation_status"], not_purchased["actual_stake_yen"]), ("NOT_PURCHASED", 0))
            retroactive = accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                                            confirmation_status=accounting.PURCHASED, stake_yen=100,
                                                            confirmed_at="2099-01-01T10:01:00+00:00", evidence_db=db, output_root=root / "retro",
                                                            confirmation_mode="RETROACTIVE_USER_CONFIRMED",
                                                            retroactive_manifest_reference={"path": "audit/migration_manifest.json", "sha256": "a" * 64})
            self.assertEqual((retroactive["confirmation_mode"], retroactive["placed_at"], retroactive["execution_odds"]), ("RETROACTIVE_USER_CONFIRMED", None, None))


class ActualSettlementTest(unittest.TestCase):
    def test_wide_confirmation_normalizes_and_not_purchased_never_settles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); intent_root = root / "intents"; output = root / "confirmations"
            intent = {"schema_version": "p2_wide_funabashi_experimental_v0_intent_v1", "policy_id": "P2_WIDE_FUNABASHI_EXPERIMENTAL_V0",
                      "date": "2099-01-01", "venue": "船橋", "race_number": 5, "race_key": RACE_KEY,
                      "reference_mode": "T15_STANDARD", "scientific_sample": True, "pair_i": 2, "pair_j": 7,
                      "recommended_stake_yen": 100, "recommendation_status": "MANUAL_BUY_RECOMMENDED", "manual_purchase_required": True}
            path = intent_root / "2099-01-01/船橋_race05_experimental.json"; path.parent.mkdir(parents=True)
            path.write_text(json.dumps(intent, ensure_ascii=False), encoding="utf-8")
            with patch.object(wide_confirm, "ROOT", root), patch.object(wide_confirm, "INTENT_ROOT", intent_root), patch.object(wide_confirm, "OUT", output), patch.object(wide_confirm, "EVIDENCE_DB", root / "none.sqlite"):
                result = wide_confirm.confirm_purchase(intent_path=path, confirm_not_purchased=True, confirmed_at=NOW)
            self.assertEqual(result["status"], "NOT_PURCHASED")
            actions = accounting.load_actual_actions(race_date="2099-01-01", venue="船橋", actual_root=root / "main", experimental_confirmation_root=output)
            self.assertEqual((len(actions), actions[0]["confirmation_status"], actions[0]["normalized_combination_key"]), (1, "NOT_PURCHASED", "2-7"))

    def test_win_wide_hit_miss_refund_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "market.sqlite"; initialize_database(db)
            _seed_official(db, root, race_key=RACE_KEY, race_number=5, payouts={("WIN", "1"): 250, ("WIDE", "1-2"): 500})
            actions = [
                {"source_id": "main", "source_sha256": "a", "ticket_type": "WIN", "selections": [1], "actual_stake_yen": 100, "normalized_combination_key": "1"},
                {"source_id": "wide", "source_sha256": "b", "ticket_type": "WIDE", "selections": [1, 3], "actual_stake_yen": 100, "normalized_combination_key": "1-3"},
            ]
            settled = accounting.settle_actual_race(race_key=RACE_KEY, purchased_actions=actions, db_path=db, settlement_root=root / "settlements", settled_at="2099-01-01T11:01:00+00:00")
            self.assertEqual([item["outcome"] for item in settled["tickets"]], ["HIT", "MISS"])
            self.assertEqual((settled["turnover_yen"], settled["gross_payout_yen"], settled["net_profit_yen"]), (200, 250, 50))
            self.assertEqual(accounting.settle_actual_race(race_key=RACE_KEY, purchased_actions=actions, db_path=db, settlement_root=root / "settlements")["status"], "IDEMPOTENT_NOOP")

    def test_daily_pending_zero_and_cumulative_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, _rec = _commit_main(root)
            pending = accounting.evaluate_actual_day(date="2099-01-01", venue="船橋", races=[5], evidence_db=db, output_root=root / "out", actual_root=root / "actual", settlement_root=root / "settlements", experimental_confirmation_root=root / "confirms", experimental_intent_roots=())
            self.assertEqual(pending["accounting_status"], "PENDING_CONFIRMATION")
            zero = accounting.evaluate_actual_day(date="2099-01-02", venue="船橋", evidence_db=root / "absent.sqlite", output_root=root / "out", actual_root=root / "actual", settlement_root=root / "settlements", experimental_confirmation_root=root / "confirms", experimental_intent_roots=())
            self.assertEqual((zero["accounting_status"], zero["turnover_yen"], zero["net_roi"]), ("COMPLETE", 0, None))
            cumulative = accounting.rebuild_actual_cumulative(through_date="2099-01-02", output_root=root / "out", generated_at="2099-01-02T12:00:00+00:00")
            self.assertIn("2026-09-01", cumulative["coverage_gap_dates"])
            self.assertEqual(cumulative["coverage_status"], "INCOMPLETE_HISTORY")

    def test_daily_complete_uses_confirmed_cash_not_recommended_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, rec = _commit_main(root); actual_root = root / "actual"; settlement_root = root / "settlements"
            _seed_official(db, root, race_key=RACE_KEY, race_number=5, payouts={("WIN", "1"): 250})
            accounting.confirm_main_purchase(recommendation_id=rec["recommendation_id"], ticket_index=1,
                                             confirmation_status=accounting.PURCHASED, stake_yen=200, confirmed_at=NOW,
                                             evidence_db=db, output_root=actual_root)
            report = accounting.evaluate_actual_day(date="2099-01-01", venue="船橋", races=[5], evidence_db=db, settlement_db=db,
                                                    output_root=root / "out", actual_root=actual_root, settlement_root=settlement_root,
                                                    experimental_confirmation_root=root / "confirms", experimental_intent_roots=(),
                                                    generated_at="2099-01-01T11:01:00+00:00")
            self.assertEqual((report["accounting_status"], report["turnover_yen"], report["gross_payout_yen"], report["net_profit_yen"]), ("COMPLETE", 200, 500, 300))
            self.assertEqual((report["net_roi"], report["recovery_rate"]), (1.5, 2.5))


if __name__ == "__main__":
    unittest.main()
