"""Isolated, append-only live development decision/result ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "db" / "live_development.sqlite"
RAW_ROOT = ROOT / "data" / "raw" / "live_development_results"


def utc_iso(value: str | datetime) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be offset-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def initialize_database(path: Path = DEFAULT_DB) -> None:
    conn = connect(path)
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS race_registry (
          race_key TEXT PRIMARY KEY, race_date TEXT NOT NULL, venue TEXT NOT NULL,
          race_number INTEGER NOT NULL, scheduled_post_time TEXT NOT NULL,
          source_entry_url TEXT, created_at TEXT NOT NULL,
          UNIQUE(race_date, venue, race_number)
        );
        CREATE TABLE IF NOT EXISTS decision_records (
          decision_id TEXT PRIMARY KEY, race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          decision_version INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('DRAFT','FROZEN','VOIDED_BEFORE_POST')),
          decision_status TEXT NOT NULL CHECK(decision_status IN ('BET','NO_BET','SHADOW_ONLY')),
          decision_created_at TEXT NOT NULL, frozen_at TEXT, decision_input_sha256 TEXT NOT NULL,
          market_snapshot_id TEXT NOT NULL, current_snapshot_id TEXT NOT NULL,
          analysis_bundle_path TEXT NOT NULL, analysis_bundle_sha256 TEXT NOT NULL,
          model_version TEXT NOT NULL, feature_set TEXT NOT NULL, model_artifact_sha256 TEXT NOT NULL,
          engineering_fixture INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
          UNIQUE(race_key, decision_version)
        );
        CREATE TABLE IF NOT EXISTS decision_runner_predictions (
          decision_id TEXT NOT NULL REFERENCES decision_records(decision_id), horse_number INTEGER NOT NULL,
          model_probability REAL NOT NULL, market_probability REAL NOT NULL, edge REAL NOT NULL, rank INTEGER NOT NULL,
          PRIMARY KEY(decision_id, horse_number)
        );
        CREATE TABLE IF NOT EXISTS decision_tickets (
          decision_ticket_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decision_records(decision_id),
          ticket_type TEXT NOT NULL, selections_json TEXT NOT NULL, stake_units REAL NOT NULL,
          reference_odds REAL, reason_codes_json TEXT NOT NULL, UNIQUE(decision_id, ticket_type, selections_json)
        );
        CREATE TABLE IF NOT EXISTS actual_bets (
          actual_bet_id TEXT PRIMARY KEY, decision_ticket_id TEXT REFERENCES decision_tickets(decision_ticket_id),
          placed_at TEXT NOT NULL, stake_units REAL NOT NULL, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS recommendation_records (
          recommendation_id TEXT PRIMARY KEY, race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          created_at TEXT NOT NULL, bundle_path TEXT NOT NULL, bundle_sha256 TEXT NOT NULL,
          recommendation_payload_sha256 TEXT NOT NULL,
          model_version TEXT NOT NULL, model_sha256 TEXT NOT NULL,
          policy_id TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
          reference_mode TEXT NOT NULL, reference_source_mark TEXT NOT NULL,
          reference_captured_at TEXT NOT NULL, seconds_to_post REAL NOT NULL,
          decision_status TEXT NOT NULL, scope_status TEXT NOT NULL, total_stake_yen INTEGER NOT NULL,
          recommendation_json TEXT NOT NULL,
          UNIQUE(race_key)
        );
        CREATE TABLE IF NOT EXISTS recommendation_tickets (
          recommendation_id TEXT NOT NULL REFERENCES recommendation_records(recommendation_id),
          ticket_index INTEGER NOT NULL, ticket_type TEXT NOT NULL, selections_json TEXT NOT NULL,
          stake_yen INTEGER NOT NULL, model_probability REAL NOT NULL, market_mass REAL NOT NULL,
          probability_ratio REAL NOT NULL, reference_odds REAL NOT NULL,
          gross_expected_return_at_snapshot REAL NOT NULL,
          PRIMARY KEY(recommendation_id, ticket_index),
          UNIQUE(recommendation_id, ticket_type, selections_json)
        );
        CREATE TABLE IF NOT EXISTS wide_research_evidence (
          research_prediction_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          created_at TEXT NOT NULL,
          reference_mode TEXT NOT NULL,
          source_mark TEXT NOT NULL,
          market_snapshot_id TEXT,
          current_snapshot_id TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          scheduled_post_time TEXT NOT NULL,
          model_bundle_sha256 TEXT NOT NULL,
          market_model_id TEXT NOT NULL,
          market_gamma REAL,
          j0_model_id TEXT NOT NULL,
          j1_model_id TEXT NOT NULL,
          pl_model_id TEXT NOT NULL,
          confirmation_scope TEXT NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          main_bundle_sha256 TEXT NOT NULL,
          UNIQUE(race_key, model_bundle_sha256)
        );
        CREATE TABLE IF NOT EXISTS wide_research_evaluations (
          research_prediction_id TEXT PRIMARY KEY REFERENCES wide_research_evidence(research_prediction_id),
          official_result_source_sha256 TEXT NOT NULL,
          evaluated_at TEXT NOT NULL,
          status TEXT NOT NULL,
          metrics_json TEXT NOT NULL,
          UNIQUE(research_prediction_id, official_result_source_sha256)
        );
        CREATE TABLE IF NOT EXISTS trio_research_evidence (
          research_prediction_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          created_at TEXT NOT NULL,
          reference_mode TEXT NOT NULL,
          source_mark TEXT NOT NULL,
          scientific_sample INTEGER NOT NULL CHECK(scientific_sample IN (0,1)),
          confirmation_scope TEXT NOT NULL,
          confirmation_eligible INTEGER NOT NULL CHECK(confirmation_eligible IN (0,1)),
          confirmation_reason TEXT NOT NULL,
          market_capture_id TEXT,
          trio_capture_id TEXT,
          current_capture_id TEXT,
          current_snapshot_id TEXT,
          captured_at TEXT NOT NULL,
          scheduled_post_time TEXT NOT NULL,
          research_bundle_sha256 TEXT NOT NULL,
          wide_joint_bundle_sha256 TEXT NOT NULL,
          tm0_model_id TEXT NOT NULL,
          tj0_model_id TEXT NOT NULL,
          tj1_model_id TEXT NOT NULL,
          tpl_model_id TEXT NOT NULL,
          tj1_beta REAL NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          main_bundle_sha256 TEXT NOT NULL,
          UNIQUE(race_key, research_bundle_sha256)
        );
        CREATE TABLE IF NOT EXISTS trio_research_evaluations (
          research_prediction_id TEXT PRIMARY KEY REFERENCES trio_research_evidence(research_prediction_id),
          official_result_source_sha256 TEXT NOT NULL,
          evaluated_at TEXT NOT NULL,
          status TEXT NOT NULL,
          metrics_json TEXT NOT NULL,
          UNIQUE(research_prediction_id, official_result_source_sha256)
        );
        CREATE TABLE IF NOT EXISTS win_research_evidence (
          research_prediction_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          created_at TEXT NOT NULL,
          reference_mode TEXT NOT NULL,
          source_mark TEXT NOT NULL,
          confirmation_scope TEXT NOT NULL,
          confirmation_eligible INTEGER NOT NULL CHECK(confirmation_eligible IN (0,1)),
          confirmation_reason TEXT,
          market_capture_id TEXT,
          current_capture_id TEXT,
          market_snapshot_id TEXT,
          current_snapshot_id TEXT,
          captured_at TEXT NOT NULL,
          scheduled_post_time TEXT NOT NULL,
          research_bundle_sha256 TEXT NOT NULL,
          confirmation_protocol_sha256 TEXT NOT NULL,
          c0_model_version TEXT NOT NULL,
          c0_model_sha256 TEXT NOT NULL,
          lambda_parameter_id TEXT NOT NULL,
          lambda_value REAL NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          main_bundle_sha256 TEXT NOT NULL,
          UNIQUE(race_key, research_bundle_sha256)
        );
        CREATE TABLE IF NOT EXISTS win_research_evaluations (
          research_prediction_id TEXT PRIMARY KEY REFERENCES win_research_evidence(research_prediction_id),
          official_result_source_sha256 TEXT NOT NULL,
          evaluated_at TEXT NOT NULL,
          status TEXT NOT NULL,
          metrics_json TEXT NOT NULL,
          UNIQUE(research_prediction_id, official_result_source_sha256)
        );
        CREATE TABLE IF NOT EXISTS current_research_evidence (
          research_prediction_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          created_at TEXT NOT NULL,
          reference_mode TEXT NOT NULL,
          source_mark TEXT NOT NULL,
          confirmation_scope TEXT NOT NULL,
          confirmation_eligible INTEGER NOT NULL CHECK(confirmation_eligible IN (0,1)),
          confirmation_reason TEXT,
          current_capture_id TEXT NOT NULL,
          current_snapshot_id TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          scheduled_post_time TEXT NOT NULL,
          research_bundle_sha256 TEXT NOT NULL,
          confirmation_protocol_sha256 TEXT NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          main_bundle_sha256 TEXT NOT NULL,
          UNIQUE(race_key, research_bundle_sha256)
        );
        CREATE TABLE IF NOT EXISTS win_market_trajectory_mark_events (
          trajectory_mark_event_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          research_version TEXT NOT NULL,
          mark TEXT NOT NULL CHECK(mark IN ('T20','T15','T10','T05','RECOVERY')),
          capture_id TEXT NOT NULL,
          snapshot_ids_json TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          scheduled_post_time TEXT NOT NULL,
          seconds_to_post REAL NOT NULL,
          raw_source_sha256 TEXT NOT NULL,
          response_sha256 TEXT NOT NULL,
          active_roster_json TEXT NOT NULL,
          confirmation_eligible INTEGER NOT NULL CHECK(confirmation_eligible IN (0,1)),
          confirmation_reason TEXT NOT NULL,
          created_at TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          UNIQUE(race_key,research_version,mark,capture_id)
        );
        CREATE TABLE IF NOT EXISTS win_market_trajectory_evidence (
          trajectory_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          research_version TEXT NOT NULL,
          created_at TEXT NOT NULL,
          materialized_at TEXT NOT NULL,
          marks_present_json TEXT NOT NULL,
          trajectory_status TEXT NOT NULL,
          roster_status TEXT NOT NULL,
          source_event_set_sha256 TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          UNIQUE(race_key,research_version)
        );
        CREATE TABLE IF NOT EXISTS win_market_lead_lag_evidence (
          lead_lag_evidence_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          created_at TEXT NOT NULL,
          status TEXT NOT NULL,
          reference_mode TEXT,
          source_mark TEXT,
          confirmation_eligible INTEGER NOT NULL CHECK(confirmation_eligible IN (0,1)),
          exclusion_reason TEXT,
          trajectory_provenance TEXT,
          t15_capture_id TEXT,
          t10_capture_id TEXT,
          t05_capture_id TEXT,
          research_bundle_sha256 TEXT NOT NULL,
          c0_model_sha256 TEXT NOT NULL,
          market_gamma REAL NOT NULL,
          main_bundle_sha256 TEXT,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          UNIQUE(race_key, research_bundle_sha256)
        );
        CREATE TABLE IF NOT EXISTS mkt_traj_ll_v1_evidence (
          cohort_evidence_id TEXT PRIMARY KEY,
          protocol_manifest_sha256 TEXT NOT NULL,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          race_date TEXT NOT NULL,
          venue TEXT NOT NULL CHECK(venue IN ('船橋','大井')),
          race_number INTEGER NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('ELIGIBLE','EXCLUDED')),
          exclusion_reason TEXT,
          t15_capture_id TEXT,
          t10_capture_id TEXT,
          t05_capture_id TEXT,
          active_roster_json TEXT,
          source_hashes_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          UNIQUE(protocol_manifest_sha256,race_key)
        );
        CREATE TABLE IF NOT EXISTS mkt_traj_ll_v1_reestimations (
          reestimation_id TEXT PRIMARY KEY,
          protocol_manifest_sha256 TEXT NOT NULL,
          venue TEXT NOT NULL CHECK(venue IN ('船橋','大井')),
          trigger_cluster_count INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          UNIQUE(protocol_manifest_sha256,venue)
        );
        CREATE TABLE IF NOT EXISTS mkt_traj_ll_v1_final_analyses (
          analysis_id TEXT PRIMARY KEY,
          protocol_manifest_sha256 TEXT NOT NULL,
          venue TEXT NOT NULL CHECK(venue IN ('船橋','大井')),
          terminal_classification TEXT NOT NULL,
          created_at TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          UNIQUE(protocol_manifest_sha256,venue)
        );
        CREATE TABLE IF NOT EXISTS result_captures (
          result_capture_id TEXT PRIMARY KEY, race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          source_url TEXT NOT NULL, captured_at TEXT NOT NULL, http_status INTEGER NOT NULL,
          content_type TEXT, raw_archive_path TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
          response_size_bytes INTEGER NOT NULL, finality_status TEXT NOT NULL,
          parser_version TEXT NOT NULL, parse_status TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(race_key, raw_sha256)
        );
        CREATE TABLE IF NOT EXISTS official_runner_results (
          result_capture_id TEXT NOT NULL REFERENCES result_captures(result_capture_id), race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          horse_number INTEGER NOT NULL, finish_position INTEGER, result_status TEXT, raw_status TEXT, parse_status TEXT NOT NULL,
          PRIMARY KEY(result_capture_id, horse_number)
        );
        CREATE TABLE IF NOT EXISTS official_payouts (
          official_payout_id TEXT PRIMARY KEY, result_capture_id TEXT NOT NULL REFERENCES result_captures(result_capture_id),
          race_key TEXT NOT NULL REFERENCES race_registry(race_key), ticket_type TEXT NOT NULL,
          combination_raw TEXT NOT NULL, canonical_combination TEXT NOT NULL, payout_raw TEXT NOT NULL,
          payout_amount INTEGER NOT NULL, payout_unit TEXT, payout_row_order INTEGER NOT NULL, parse_status TEXT NOT NULL,
          UNIQUE(result_capture_id, ticket_type, canonical_combination),
          UNIQUE(result_capture_id, ticket_type, payout_row_order)
        );
        CREATE TABLE IF NOT EXISTS result_completeness_evidence (
          result_completeness_evidence_id TEXT PRIMARY KEY,
          race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          raw_sha256 TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          result_source_state TEXT NOT NULL,
          model_history_state TEXT NOT NULL,
          win_payout_state TEXT NOT NULL,
          wide_payout_state TEXT NOT NULL,
          trio_payout_state TEXT NOT NULL,
          reason_codes_json TEXT NOT NULL,
          source_reference_json TEXT NOT NULL,
          assessment_payload_sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(race_key, raw_sha256)
        );
        CREATE TABLE IF NOT EXISTS strategy_settlements (
          settlement_id TEXT PRIMARY KEY, race_key TEXT NOT NULL REFERENCES race_registry(race_key),
          strategy_source TEXT NOT NULL, strategy_source_id TEXT NOT NULL,
          strategy_payload_sha256 TEXT,
          official_result_source_sha256 TEXT NOT NULL, official_payout_source_sha256 TEXT,
          settlement_status TEXT NOT NULL, decision_status TEXT, reference_mode TEXT,
          total_stake_yen INTEGER NOT NULL, gross_return_yen INTEGER NOT NULL, pnl_yen INTEGER NOT NULL,
          return_rate REAL, roi REAL, created_at TEXT NOT NULL,
          UNIQUE(race_key, strategy_source_id)
        );
        CREATE TABLE IF NOT EXISTS ticket_settlements (
          settlement_id TEXT NOT NULL REFERENCES strategy_settlements(settlement_id),
          ticket_index INTEGER NOT NULL, ticket_type TEXT NOT NULL, selections_json TEXT NOT NULL,
          stake_yen INTEGER NOT NULL, official_payout_per_100_yen INTEGER,
          settlement_status TEXT NOT NULL, gross_return_yen INTEGER NOT NULL, pnl_yen INTEGER NOT NULL,
          PRIMARY KEY(settlement_id, ticket_index),
          UNIQUE(settlement_id, ticket_type, selections_json)
        );
        CREATE TABLE IF NOT EXISTS strategy_win_evaluations (
          settlement_id TEXT PRIMARY KEY REFERENCES strategy_settlements(settlement_id),
          winner_horse_number INTEGER NOT NULL, candidate_probability REAL NOT NULL,
          market_probability REAL NOT NULL, candidate_ll REAL NOT NULL, market_ll REAL NOT NULL,
          delta_ll REAL NOT NULL, reference_bucket TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reconciliations (
          race_key TEXT PRIMARY KEY REFERENCES race_registry(race_key), reconciliation_status TEXT NOT NULL,
          decision_id TEXT REFERENCES decision_records(decision_id), result_capture_id TEXT REFERENCES result_captures(result_capture_id),
          evaluation_eligible INTEGER NOT NULL, reason_code TEXT NOT NULL, reconciled_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operational_events (
          event_id TEXT PRIMARY KEY, race_key TEXT REFERENCES race_registry(race_key), event_type TEXT NOT NULL,
          occurred_at TEXT NOT NULL, detail_json TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS prevent_frozen_decision_update
        BEFORE UPDATE ON decision_records
        WHEN OLD.state = 'FROZEN'
        BEGIN SELECT RAISE(ABORT, 'frozen decision is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_frozen_prediction_update
        BEFORE UPDATE ON decision_runner_predictions
        WHEN EXISTS (SELECT 1 FROM decision_records d WHERE d.decision_id = OLD.decision_id AND d.state = 'FROZEN')
        BEGIN SELECT RAISE(ABORT, 'frozen decision prediction is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_frozen_ticket_update
        BEFORE UPDATE ON decision_tickets
        WHEN EXISTS (SELECT 1 FROM decision_records d WHERE d.decision_id = OLD.decision_id AND d.state = 'FROZEN')
        BEGIN SELECT RAISE(ABORT, 'frozen decision ticket is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_frozen_prediction_delete
        BEFORE DELETE ON decision_runner_predictions
        WHEN EXISTS (SELECT 1 FROM decision_records d WHERE d.decision_id = OLD.decision_id AND d.state = 'FROZEN')
        BEGIN SELECT RAISE(ABORT, 'frozen decision prediction is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_frozen_ticket_delete
        BEFORE DELETE ON decision_tickets
        WHEN EXISTS (SELECT 1 FROM decision_records d WHERE d.decision_id = OLD.decision_id AND d.state = 'FROZEN')
        BEGIN SELECT RAISE(ABORT, 'frozen decision ticket is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_recommendation_record_update
        BEFORE UPDATE ON recommendation_records
        BEGIN SELECT RAISE(ABORT, 'recommendation evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_recommendation_record_delete
        BEFORE DELETE ON recommendation_records
        BEGIN SELECT RAISE(ABORT, 'recommendation evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_recommendation_ticket_update
        BEFORE UPDATE ON recommendation_tickets
        BEGIN SELECT RAISE(ABORT, 'recommendation evidence ticket is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_recommendation_ticket_delete
        BEFORE DELETE ON recommendation_tickets
        BEGIN SELECT RAISE(ABORT, 'recommendation evidence ticket is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_wide_research_evidence_update
        BEFORE UPDATE ON wide_research_evidence
        BEGIN SELECT RAISE(ABORT, 'wide research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_wide_research_evidence_delete
        BEFORE DELETE ON wide_research_evidence
        BEGIN SELECT RAISE(ABORT, 'wide research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_wide_research_evaluation_update
        BEFORE UPDATE ON wide_research_evaluations
        BEGIN SELECT RAISE(ABORT, 'wide research evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_wide_research_evaluation_delete
        BEFORE DELETE ON wide_research_evaluations
        BEGIN SELECT RAISE(ABORT, 'wide research evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_trio_research_evidence_update
        BEFORE UPDATE ON trio_research_evidence
        BEGIN SELECT RAISE(ABORT, 'trio research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_trio_research_evidence_delete
        BEFORE DELETE ON trio_research_evidence
        BEGIN SELECT RAISE(ABORT, 'trio research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_trio_research_evaluation_update
        BEFORE UPDATE ON trio_research_evaluations
        BEGIN SELECT RAISE(ABORT, 'trio research evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_trio_research_evaluation_delete
        BEFORE DELETE ON trio_research_evaluations
        BEGIN SELECT RAISE(ABORT, 'trio research evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_research_evidence_update
        BEFORE UPDATE ON win_research_evidence
        BEGIN SELECT RAISE(ABORT, 'win research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_research_evidence_delete
        BEFORE DELETE ON win_research_evidence
        BEGIN SELECT RAISE(ABORT, 'win research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_research_evaluation_update
        BEFORE UPDATE ON win_research_evaluations
        BEGIN SELECT RAISE(ABORT, 'win research evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_research_evaluation_delete
        BEFORE DELETE ON win_research_evaluations
        BEGIN SELECT RAISE(ABORT, 'win research evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_current_research_evidence_update
        BEFORE UPDATE ON current_research_evidence
        BEGIN SELECT RAISE(ABORT, 'current research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_current_research_evidence_delete
        BEFORE DELETE ON current_research_evidence
        BEGIN SELECT RAISE(ABORT, 'current research evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_market_trajectory_mark_event_update
        BEFORE UPDATE ON win_market_trajectory_mark_events
        BEGIN SELECT RAISE(ABORT, 'win market trajectory mark event is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_market_trajectory_mark_event_delete
        BEFORE DELETE ON win_market_trajectory_mark_events
        BEGIN SELECT RAISE(ABORT, 'win market trajectory mark event is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_mkt_traj_ll_v1_evidence_update
        BEFORE UPDATE ON mkt_traj_ll_v1_evidence
        BEGIN SELECT RAISE(ABORT, 'mkt trajectory lead lag v1 evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_mkt_traj_ll_v1_evidence_delete
        BEFORE DELETE ON mkt_traj_ll_v1_evidence
        BEGIN SELECT RAISE(ABORT, 'mkt trajectory lead lag v1 evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_mkt_traj_ll_v1_reestimation_update
        BEFORE UPDATE ON mkt_traj_ll_v1_reestimations
        BEGIN SELECT RAISE(ABORT, 'mkt trajectory lead lag v1 reestimation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_mkt_traj_ll_v1_reestimation_delete
        BEFORE DELETE ON mkt_traj_ll_v1_reestimations
        BEGIN SELECT RAISE(ABORT, 'mkt trajectory lead lag v1 reestimation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_mkt_traj_ll_v1_final_analysis_update
        BEFORE UPDATE ON mkt_traj_ll_v1_final_analyses
        BEGIN SELECT RAISE(ABORT, 'mkt trajectory lead lag v1 final analysis is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_mkt_traj_ll_v1_final_analysis_delete
        BEFORE DELETE ON mkt_traj_ll_v1_final_analyses
        BEGIN SELECT RAISE(ABORT, 'mkt trajectory lead lag v1 final analysis is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_market_lead_lag_evidence_update
        BEFORE UPDATE ON win_market_lead_lag_evidence
        BEGIN SELECT RAISE(ABORT, 'win market lead lag evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_win_market_lead_lag_evidence_delete
        BEFORE DELETE ON win_market_lead_lag_evidence
        BEGIN SELECT RAISE(ABORT, 'win market lead lag evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_strategy_settlement_update
        BEFORE UPDATE ON strategy_settlements
        BEGIN SELECT RAISE(ABORT, 'strategy settlement is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_strategy_settlement_delete
        BEFORE DELETE ON strategy_settlements
        BEGIN SELECT RAISE(ABORT, 'strategy settlement is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_ticket_settlement_update
        BEFORE UPDATE ON ticket_settlements
        BEGIN SELECT RAISE(ABORT, 'ticket settlement is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_ticket_settlement_delete
        BEFORE DELETE ON ticket_settlements
        BEGIN SELECT RAISE(ABORT, 'ticket settlement is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_strategy_win_evaluation_update
        BEFORE UPDATE ON strategy_win_evaluations
        BEGIN SELECT RAISE(ABORT, 'strategy win evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_strategy_win_evaluation_delete
        BEFORE DELETE ON strategy_win_evaluations
        BEGIN SELECT RAISE(ABORT, 'strategy win evaluation is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_result_completeness_evidence_update
        BEFORE UPDATE ON result_completeness_evidence
        BEGIN SELECT RAISE(ABORT, 'result completeness evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_result_completeness_evidence_delete
        BEFORE DELETE ON result_completeness_evidence
        BEGIN SELECT RAISE(ABORT, 'result completeness evidence is immutable'); END;
        """)
        conn.commit()
    finally:
        conn.close()


def register_race(conn: sqlite3.Connection, race: dict[str, Any]) -> None:
    conn.execute("""INSERT INTO race_registry(race_key,race_date,venue,race_number,scheduled_post_time,source_entry_url,created_at)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(race_key) DO NOTHING""",
        (race["race_key"], race["race_date"], race["venue"], int(race["race_number"]), utc_iso(race["scheduled_post_time"]), race.get("source_entry_url"), utc_iso(datetime.now(timezone.utc))))


def archive_raw(race_key: str, raw: bytes, captured_at: str) -> tuple[str, str, int]:
    digest = hashlib.sha256(raw).hexdigest(); stamp = utc_iso(captured_at).replace(":", "").replace("+00:00", "Z").replace("-", "")
    path = RAW_ROOT / race_key / f"result_{stamp}_{digest}.html"; path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".tmp"); temporary.write_bytes(raw); temporary.replace(path)
    return digest, str(path.relative_to(ROOT)), len(raw)


def event(conn: sqlite3.Connection, race_key: str | None, event_type: str, detail: dict[str, Any]) -> None:
    conn.execute("INSERT INTO operational_events VALUES(?,?,?,?,?)", (str(uuid.uuid4()), race_key, event_type, utc_iso(datetime.now(timezone.utc)), json.dumps(detail, ensure_ascii=False, sort_keys=True)))


def canonical_combination(ticket_type: str, raw: str) -> str:
    numbers = [int(item) for item in raw.replace("－", "-").split("-") if item.strip().isdigit()]
    expected = {"WIN": 1, "WIDE": 2, "TRIO": 3}.get(ticket_type)
    if expected is None or len(numbers) != expected or len(set(numbers)) != expected:
        raise ValueError(f"invalid {ticket_type} official combination: {raw}")
    return "-".join(map(str, sorted(numbers)))
