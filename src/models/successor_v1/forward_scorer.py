"""Exact fixed Fold4 M2/race-head/EB/Plackett-Luce forward scorer."""

from __future__ import annotations

import hashlib
import itertools
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from src.features.online.successor_v1_forward_adapter import (
    PRIMARY_CATEGORICAL, PRIMARY_HASH, PRIMARY_NAMES,
    RACE_HEAD_CATEGORICAL, RACE_HEAD_HASH, RACE_HEAD_NAMES,
    reject_outcome_fields, validate_exact_frame,
)
from src.models.successor_v1.eb_state import backfit, score_effects


ROOT = Path(__file__).resolve().parents[3]
M2_PATH = ROOT / "audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/models/m2_outer_fold4.cbm"
RACE_HEAD_PATH = ROOT / "audit/successor_v1/job004/attempts/attempt_training_004/checkpoints/models/race_head_outer_fold4.cbm"
EB_COMPONENT_PATH = ROOT / "audit/successor_v1/job004/attempts/attempt_training_003/checkpoints/eb/fold4_components.json"
M2_SHA = "0eab5da875ed4155c7b4f5b92c21d6b8893b821abaef18d0f69f37e20ef4ebf2"
RACE_HEAD_SHA = "58357312e69516e57c52121ec57c64093a686e101e2d0b3ae0fc0e482e6d41ec"
EB_COMPONENT_SHA = "b2e56f153e0ce0b056e3117f52e50d9e841da0e33e0831244ff67516f543bab2"
M0_T0 = 0.44022846403852645
M1_T0 = 0.44167862602822466
GAMMA = 0.02721867845067733
UPSET_MEAN = 0.8460234339580412
UPSET_SIGMA = 0.054628106852266066
MASS_TOLERANCE = 1e-10


class ForwardScorerError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file() or sha256_file(path) != expected:
        raise ForwardScorerError(f"FROZEN_ARTIFACT_HASH_MISMATCH:{path}")


def preprocess(frame: pd.DataFrame, names: list[str], categorical: list[str]) -> pd.DataFrame:
    output = frame.loc[:, names].copy()
    for name in categorical:
        output[name] = output[name].fillna("__MISSING__").astype(str)
    for name in set(names) - set(categorical):
        output[name] = pd.to_numeric(output[name], errors="coerce")
    return output


def compute_raw_m2_score(model: Any, primary: pd.DataFrame) -> np.ndarray:
    reject_outcome_fields(primary.columns)
    validate_exact_frame(primary, PRIMARY_NAMES, PRIMARY_HASH)
    return np.asarray(model.predict(preprocess(primary, PRIMARY_NAMES, PRIMARY_CATEGORICAL)), dtype=np.float64)


def compute_race_head_score(model: Any, race_head: pd.DataFrame) -> float:
    reject_outcome_fields(race_head.columns)
    validate_exact_frame(race_head, RACE_HEAD_NAMES, RACE_HEAD_HASH, newline_joined=True)
    return float(np.asarray(model.predict(preprocess(race_head, RACE_HEAD_NAMES, RACE_HEAD_CATEGORICAL)))[0])


def exact_pl_distribution(scores: Iterable[float], temperature: float) -> tuple[np.ndarray, dict[tuple[int, int], float]]:
    scores = np.asarray(list(scores), dtype=np.float64)
    if len(scores) < 3 or temperature <= 0 or np.any(~np.isfinite(scores)):
        raise ForwardScorerError("INVALID_PL_INPUT")
    weights = np.exp(scores / temperature - np.max(scores / temperature))
    total = float(weights.sum()); n = len(scores)
    ordered = np.zeros((n, n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            if j == i:
                continue
            for k in range(n):
                if k != i and k != j:
                    ordered[i, j, k] = weights[i] / total * weights[j] / (total - weights[i]) * weights[k] / (total - weights[i] - weights[j])
    runner = ordered.sum(axis=(1, 2)) + ordered.sum(axis=(0, 2)) + ordered.sum(axis=(0, 1))
    pairs: dict[tuple[int, int], float] = {}
    for a, b in itertools.combinations(range(n), 2):
        pairs[(a, b)] = float(sum(
            ordered[a, b, k] + ordered[b, a, k] + ordered[a, k, b] +
            ordered[b, k, a] + ordered[k, a, b] + ordered[k, b, a]
            for k in range(n) if k not in {a, b}
        ))
    if abs(float(ordered.sum()) - 1.0) > MASS_TOLERANCE or abs(sum(pairs.values()) - 3.0) > MASS_TOLERANCE:
        raise ForwardScorerError("PL_MASS_VIOLATION")
    return runner, pairs


def temperature_for_race(n: int, race_head_score: float | None = None) -> tuple[float, str]:
    if n == 3:
        return M0_T0, "M0_T0"
    if race_head_score is None or not math.isfinite(race_head_score):
        raise ForwardScorerError("RACE_HEAD_SCORE_REQUIRED")
    z = float(np.clip((race_head_score - UPSET_MEAN) / UPSET_SIGMA, -3.0, 3.0))
    return M1_T0 * math.exp(GAMMA * z), "M1_MODULATED"


def q_model_from_pairs(pairs: Mapping[tuple[int, int], float]) -> dict[tuple[int, int], float]:
    output = {key: value / 3.0 for key, value in pairs.items()}
    if abs(sum(output.values()) - 1.0) > MASS_TOLERANCE:
        raise ForwardScorerError("Q_MODEL_MASS_VIOLATION")
    return output


def rebuild_eb_before_date(observations: pd.DataFrame, target_date: str, fixed_components: dict[str, tuple[float, float]]) -> Any:
    used = observations[pd.to_datetime(observations["race_date"]).dt.date < date.fromisoformat(target_date)]
    return backfit(
        used["residual"].to_numpy(dtype=np.float64), used["horse_key"].to_numpy(object),
        used["jockey_key"].to_numpy(object), used["venue"].to_numpy(object),
        mode="FIXED_COMPONENT", fixed_components=fixed_components, max_cycles=20, tolerance=1e-5,
    )


def score_eb(state: Any, horse: Iterable[Any], jockey: Iterable[Any], venue: Iterable[Any]) -> np.ndarray:
    return score_effects(state, np.asarray(list(horse), object), np.asarray(list(jockey), object), np.asarray(list(venue), object))
