"""Accepted JOB007R3 scorer continuation used by the isolated live worker."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from src.audit import p2s_job007_stage2_locked_replay as r3
from src.evaluation.successor_v1_stage2_prequential import (
    CalibrationRow, MAPPINGS, calibrated_market, fit_mapping_parameters, hybrid,
    market_q_raw, winning_pairs,
)
from src.features.online.successor_v1_forward_adapter import (
    PRIMARY_HASH, RACE_HEAD_HASH, Primary129ForwardState,
)
from src.models.successor_v1.forward_scorer import (
    EB_COMPONENT_SHA, GAMMA, M0_T0, M1_T0, M2_PATH, M2_SHA, RACE_HEAD_PATH,
    RACE_HEAD_SHA, UPSET_MEAN, UPSET_SIGMA, compute_race_head_score,
    compute_raw_m2_score, exact_pl_distribution, q_model_from_pairs,
    rebuild_eb_before_date, require_hash, score_eb, temperature_for_race,
)
from src.operations.stage2_confirmatory_live import (
    DEVELOPMENT_ROOT, ConfirmatoryLiveError, _immutable_json, sha256_file,
)


class AcceptedR3LiveScorer:
    """Rebuild strict-prior state and apply the immutable Fold4 scorer."""

    def __init__(self, *, market_db: Path, output_root: Path) -> None:
        self.market_db, self.output_root = market_db, output_root
        require_hash(M2_PATH, M2_SHA); require_hash(RACE_HEAD_PATH, RACE_HEAD_SHA)
        self.m2 = CatBoostRegressor(); self.m2.load_model(str(M2_PATH))
        self.race_head = CatBoostRegressor(); self.race_head.load_model(str(RACE_HEAD_PATH))
        self.fixed_components = r3._fixed_components()
        self.known_horses = r3._reference_horse_keys()
        self._target_date: str | None = None
        self.feature_state: Primary129ForwardState | None = None
        self.ledger: pd.DataFrame | None = None

    def _rebuild_state(self, target_date: str) -> None:
        if self._target_date == target_date:
            return
        from src.audit.p2s_job003_materialized_feature_foundation import class_values, get_races

        state = Primary129ForwardState.from_historical_races(get_races())
        ledger_path = self.output_root / "state/eb_residual_observations.csv.gz"
        ledger = pd.read_csv(ledger_path, compression="gzip", low_memory=False)
        existing = {(str(row.race_key), int(row.horse_number)) for row in ledger.itertuples()}
        raw = r3._readonly(r3.LIVE_HISTORY_DB)
        normalized = r3._readonly(r3.NORMALIZED_HISTORY_DB)
        new_ledger: list[dict[str, Any]] = []
        try:
            dates = [str(row[0]) for row in normalized.execute(
                "SELECT DISTINCT race_date FROM races WHERE venue_class='NANKAN_TARGET' AND race_date>'2026-07-31' AND race_date<? ORDER BY race_date",
                (target_date,),
            )]
            for race_date in dates:
                settled = r3._normalized_date_rows(normalized, race_date)
                state_races: list[dict[str, Any]] = []
                strengths: dict[str, float | None] = {}
                for normalized_race in settled:
                    starters = r3._starter_rows(normalized_race["result_runners"])
                    if len(starters) < 3:
                        raise ConfirmatoryLiveError(f"LIVE_EB_STATE_STARTERS_LT_3:{normalized_race['race_key']}")
                    capture = r3._selected_card_capture(raw, str(normalized_race["card_capture_path"]))
                    html, _ = r3._verified_card(capture)
                    identity = r3.official.resolve_race(str(capture["source_url"]), html)
                    identity_rows = {int(row["horse_number"]): row for row in starters}
                    safe_race, _ = r3._card_target_race(
                        html=html, identity=identity, identity_rows=identity_rows,
                        active_numbers=set(identity_rows), race_key=str(normalized_race["race_key"]),
                        known_horses=self.known_horses, source_mode="POST_SETTLEMENT_EB_UPDATE",
                    )
                    adapted = state.materialize_race(safe_race)
                    missing_residual = any((safe_race["race_key"], int(row["horse_number"])) not in existing for row in safe_race["runners"])
                    if missing_residual:
                        raw_score = compute_raw_m2_score(self.m2, adapted.primary)
                        z_by_number = r3._target_z(starters)
                        for index, runner in enumerate(sorted(safe_race["runners"], key=lambda row: int(row["horse_number"]))):
                            number = int(runner["horse_number"])
                            new_ledger.append({"race_date": race_date, "race_key": safe_race["race_key"], "horse_number": number, "residual": z_by_number[number] - float(raw_score[index]), "horse_key": runner["horse_key"], "jockey_key": runner["jockey"], "venue": r3.VENUE_CODES[safe_race["venue"]]})
                            existing.add((safe_race["race_key"], number))
                    outcome = {int(row["horse_number"]): row for row in starters}
                    settled_runners = []
                    for runner in sorted(safe_race["runners"], key=lambda row: int(row["horse_number"])):
                        result = outcome[int(runner["horse_number"])]
                        settled_runners.append(runner | {
                            "finish_position": result.get("finish_position"),
                            "result_status": "FINISHED" if result["starter_status"] == "STARTER_VALID_FINISH" else "DNF",
                            "finish_time_seconds": result.get("finish_time_seconds"),
                            "last_3f": result.get("last_3f"), "margin_raw": result.get("margin_raw"),
                        })
                    settled_race = safe_race | {"runners": settled_runners, "corners_json": "[]"}
                    settled_race["_class"] = class_values(settled_race)
                    state_races.append(settled_race)
                    value = adapted.primary.iloc[0]["comp_ability_mean"]
                    strengths[safe_race["race_key"]] = float(value) if pd.notna(value) else None
                state.update_settled_date(state_races, field_strengths=strengths)
        finally:
            raw.close(); normalized.close()
        if new_ledger:
            ledger = pd.concat([ledger, pd.DataFrame(new_ledger)], ignore_index=True)
            r3._write_eb_ledger(ledger_path, ledger)
        self.feature_state, self.ledger, self._target_date = state, ledger, target_date

    @staticmethod
    def _prediction_pair_data(prediction: Mapping[str, Any]) -> tuple[list[tuple[int, int]], tuple[float, ...]]:
        order = [(int(row["horse_number_1"]), int(row["horse_number_2"])) for row in prediction["pairs"]]
        return order, tuple(float(row["q_model"]) for row in prediction["pairs"])

    def _target_labels(self, prediction: Mapping[str, Any], normalized: sqlite3.Connection) -> list[tuple[int, int]] | None:
        rows = r3._normalized_date_rows(normalized, str(prediction["race_date"]))
        matches = [row for row in rows if str(row["venue"]) == str(prediction["venue"]) and int(row["race_number"]) == int(prediction["race_number"])]
        if len(matches) != 1:
            return None
        starters = r3._starter_rows(matches[0]["result_runners"])
        top3 = [int(row["horse_number"]) for rank in (1, 2, 3) for row in starters if row["starter_status"] == "STARTER_VALID_FINISH" and row["finish_position"] == rank]
        order, _ = self._prediction_pair_data(prediction)
        try:
            return winning_pairs(top3, order)
        except Exception as exc:
            if "HARD_RECONCILIATION_BLOCK" in str(exc):
                raise
            return None

    def _calibrations_before(self, target_date: str) -> dict[str, list[CalibrationRow]]:
        output = {mapping: [] for mapping in MAPPINGS}
        normalized = r3._readonly(r3.NORMALIZED_HISTORY_DB)
        try:
            roots = (
                (DEVELOPMENT_ROOT / "predictions", DEVELOPMENT_ROOT / "reconciliation"),
                (self.output_root / "predictions", self.output_root / "reconciliation"),
            )
            for prediction_root, reconciliation_root in roots:
                for path in sorted(prediction_root.glob("20??-??-??/*.json")):
                    if path.name == "_DATE_FROZEN.json":
                        continue
                    prediction = json.loads(path.read_text(encoding="utf-8"))
                    if str(prediction["race_date"]) >= target_date:
                        continue
                    reconciliation = reconciliation_root / str(prediction["race_date"]) / path.name
                    labels: list[tuple[int, int]] | None = None
                    if reconciliation.exists():
                        value = json.loads(reconciliation.read_text(encoding="utf-8"))
                        if value.get("target_status") == "VALID_TARGET":
                            labels = [tuple(int(item) for item in pair) for pair in value["winning_pair_labels"]]
                    else:
                        labels = self._target_labels(prediction, normalized)
                        if labels is not None and prediction_root == self.output_root / "predictions":
                            _immutable_json(reconciliation, {
                                "prediction_artifact_sha256": sha256_file(path),
                                "winning_pair_labels": labels, "target_status": "VALID_TARGET",
                                "formal_performance_evaluated": False, "payout_accessed": False,
                            })
                    if labels is None:
                        continue
                    order, q_model = self._prediction_pair_data(prediction)
                    indexes = tuple(order.index(tuple(pair)) for pair in labels)
                    for mapping in MAPPINGS:
                        output[mapping].append(CalibrationRow(str(prediction["race_date"]), tuple(float(item) for item in prediction["market_mappings"][mapping]["q_raw"]), q_model, indexes))
        finally:
            normalized.close()
        return output

    def score(self, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        target_date = str(candidate["race_date"])
        self._rebuild_state(target_date)
        assert self.feature_state is not None and self.ledger is not None
        calibrations = self._calibrations_before(target_date)
        parameters = {mapping: fit_mapping_parameters(calibrations[mapping], target_date) for mapping in MAPPINGS}
        market = r3._readonly(self.market_db)
        try:
            bundle = r3._load_t15_bundle(market, dict(candidate), self.known_horses)
        finally:
            market.close()
        adapted = self.feature_state.materialize_race(bundle["race"])
        raw_score = compute_raw_m2_score(self.m2, adapted.primary)
        runners = sorted(bundle["race"]["runners"], key=lambda row: int(row["horse_number"]))
        eb_state = rebuild_eb_before_date(self.ledger, target_date, self.fixed_components)
        eb_effect = score_eb(eb_state, [row["horse_key"] for row in runners], [row["jockey"] for row in runners], [r3.VENUE_CODES[bundle["race"]["venue"]]] * len(runners))
        eb_score = raw_score + eb_effect
        head = None if len(runners) == 3 else compute_race_head_score(self.race_head, adapted.race_head)
        temperature, temperature_rule = temperature_for_race(len(runners), head)
        runner_probability, indexed_pairs = exact_pl_distribution(eb_score, temperature)
        horses = [int(row["horse_number"]) for row in runners]
        pair_probability = {tuple(sorted((horses[a], horses[b]))): probability for (a, b), probability in indexed_pairs.items()}
        q_map = q_model_from_pairs(pair_probability)
        pair_order = [(row["horse_number_1"], row["horse_number_2"]) for row in bundle["pairs"]]
        q_model = np.asarray([q_map[pair] for pair in pair_order], dtype=np.float64)
        mappings: dict[str, Any] = {}
        for mapping in MAPPINGS:
            q_raw = market_q_raw([row["lower_odds"] for row in bundle["pairs"]], [row["upper_odds"] for row in bundle["pairs"]], mapping)
            parameter = parameters[mapping]
            q_market = calibrated_market(q_raw, float(parameter["gamma"]))
            q_hybrid = hybrid(q_market, q_model, float(parameter["beta"]))
            mappings[mapping] = {"q_raw": q_raw.tolist(), "gamma_used": parameter["gamma"], "beta_used": parameter["beta"], "q_market": q_market.tolist(), "q_hybrid": q_hybrid.tolist()}
        primary = parameters[MAPPINGS[0]]
        return {
            "scheduled_post_time": bundle["scheduled_post_time"],
            "current_snapshot_id": bundle["current_snapshot_id"],
            "target_static_source_capture_id": bundle["static_capture_id"],
            "target_static_source_raw_sha256": bundle["static_raw_sha256"],
            "wide_capture_id": bundle["wide_capture_id"], "wide_capture_raw_sha256": bundle["wide_raw_sha256"],
            "active_t15_roster": horses,
            "roster_sha256": hashlib.sha256(json.dumps(horses, separators=(",", ":")).encode()).hexdigest(),
            "primary129_ordered_sha256": PRIMARY_HASH, "racehead32_ordered_sha256": RACE_HEAD_HASH,
            "m2_artifact_sha256": M2_SHA, "race_head_artifact_sha256": RACE_HEAD_SHA,
            "eb_component_sha256": EB_COMPONENT_SHA,
            "eb_observation_ledger_sha256": hashlib.sha256(self.ledger.to_csv(index=False).encode()).hexdigest(),
            "fold4_parameters": {"m0_t0": M0_T0, "m1_t0": M1_T0, "gamma": GAMMA, "upset_mean": UPSET_MEAN, "upset_sigma": UPSET_SIGMA},
            "temperature_rule": temperature_rule,
            "runners": [{"horse_number": horses[index], "raw_score": float(raw_score[index]), "eb_score": float(eb_score[index]), "top3_probability": float(runner_probability[index]), **bundle["provenance"]["runner_sources"][index]} for index in range(len(horses))],
            "pairs": [{"horse_number_1": pair[0], "horse_number_2": pair[1], "p_wide": float(pair_probability[pair]), "q_model": float(q_map[pair])} for pair in pair_order],
            "market_mappings": mappings,
            "prior_calibration_race_count": int(primary["prior_races"]),
            "prior_calibration_date_count": int(primary["prior_dates"]),
            "warmup_status": bool(primary["warmup"]),
            **{key: value for key, value in bundle["provenance"].items() if key != "runner_sources"},
        }
