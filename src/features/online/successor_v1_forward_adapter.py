"""Provider-injected exact feature boundary for the frozen Fold4 scorer."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
PRIMARY_COUNT = 129
PRIMARY_HASH = "f2d11d6632c94c3826343f5ce3051ebb9d21d26b2c5754ea38a6f06c20604aa5"
RACE_HEAD_COUNT = 32
RACE_HEAD_HASH = "d65c205307ea63b58b3f284530d6daa747f04bb3411c068c3430735860a11303"
MODES = {"T15_PREDICTION", "POST_SETTLEMENT_EB_UPDATE"}
LIVE_T15_UNRESOLVED_PRIMARY_FIELDS = (
    "log_prize_1",
    "log_prize_total",
    "jockey_affiliation",
)
OUTCOME_FIELDS = {
    "finish_position", "result_status", "payout", "payouts", "settlement",
    "target_z", "actual_top3", "winning_pairs", "wide_hit",
}


class ForwardAdapterError(RuntimeError):
    pass


def require_live_t15_primary_sources(resolved_fields: Iterable[str]) -> None:
    """Fail closed until frozen-equivalent pre-race sources are implemented.

    The existing live materializer supplies the legacy 178-feature contract.
    It has no frozen Job003B-equivalent source mapping for these Primary129
    target fields.  Treating a legacy token or an absent prize as equivalent
    would change the validated scorer, so the adapter may not synthesize them.
    """
    missing = sorted(set(LIVE_T15_UNRESOLVED_PRIMARY_FIELDS) - set(resolved_fields))
    if missing:
        raise ForwardAdapterError(f"PRIMARY129_TARGET_SOURCE_UNRESOLVED:{','.join(missing)}")


class HistorySource(Protocol):
    def materialize(self, target_rows: pd.DataFrame, target_date: str) -> pd.DataFrame: ...
    @property
    def max_history_date(self) -> str | None: ...


class TargetRaceSource(Protocol):
    def target_rows(self, race_key: str, target_date: str, mode: str) -> pd.DataFrame: ...


def _ordered_manifest(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: int(row["ordered_position"]))
    return [row["feature_name"] for row in rows if row.get("included", "True").lower() != "false"]


def ordered_hash(names: Iterable[str], *, newline_joined: bool = False) -> str:
    values = list(names)
    payload = ("\n".join(values).encode() if newline_joined else
               json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode())
    return hashlib.sha256(payload).hexdigest()


PRIMARY_NAMES = _ordered_manifest(ROOT / "data/manifests/successor_v1/PRIMARY_MODEL_INPUT_MANIFEST_V1.csv")
RACE_HEAD_NAMES = _ordered_manifest(ROOT / "data/manifests/successor_v1/RACE_HEAD_INPUT_MANIFEST_V1.csv")
if len(PRIMARY_NAMES) != PRIMARY_COUNT or ordered_hash(PRIMARY_NAMES) != PRIMARY_HASH:
    raise ForwardAdapterError("PRIMARY129_MANIFEST_MISMATCH")
if len(RACE_HEAD_NAMES) != RACE_HEAD_COUNT or ordered_hash(RACE_HEAD_NAMES, newline_joined=True) != RACE_HEAD_HASH:
    raise ForwardAdapterError("RACEHEAD32_MANIFEST_MISMATCH")


def categorical_names(model: str) -> list[str]:
    path = ROOT / "data/manifests/successor_v1/CATBOOST_INPUT_ROLE_MANIFEST_V1.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [row["feature_name"] for row in rows if row["model"] == model and row["feature_role"] == "CATEGORICAL"]


PRIMARY_CATEGORICAL = categorical_names("PRIMARY")
RACE_HEAD_CATEGORICAL = ["venue", "race_type", "surface", "direction", "class_code", "age_condition_code"]


def reject_outcome_fields(columns: Iterable[str]) -> None:
    bad = [name for name in columns if name.lower() in OUTCOME_FIELDS]
    if bad:
        raise ForwardAdapterError(f"OUTCOME_FIELD_FORBIDDEN:{','.join(sorted(bad))}")


def validate_history_boundary(max_history_date: str | None, target_date: str) -> None:
    if max_history_date is not None and date.fromisoformat(max_history_date) >= date.fromisoformat(target_date):
        raise ForwardAdapterError("SAME_DAY_OR_FUTURE_HISTORY")


def validate_exact_frame(frame: pd.DataFrame, names: Sequence[str], expected_hash: str, *, newline_joined: bool = False) -> None:
    if list(frame.columns) != list(names):
        raise ForwardAdapterError("FEATURE_ORDER_MISMATCH")
    if ordered_hash(names, newline_joined=newline_joined) != expected_hash:
        raise ForwardAdapterError("FEATURE_HASH_MISMATCH")
    if len(names) == 178:
        raise ForwardAdapterError("LEGACY178_FORBIDDEN")


@dataclass(frozen=True)
class AdaptedRace:
    race_key: str
    race_date: str
    horse_numbers: tuple[int, ...]
    primary: pd.DataFrame
    race_head: pd.DataFrame
    max_history_date: str | None


def adapt_materialized_rows(rows: pd.DataFrame) -> AdaptedRace:
    if rows.empty or rows["race_key"].nunique() != 1 or rows["race_date"].nunique() != 1:
        raise ForwardAdapterError("TARGET_RACE_ROWS_INVALID")
    rows = rows.sort_values("horse_number", kind="stable").reset_index(drop=True)
    target_date = str(rows.loc[0, "race_date"])[:10]
    max_dates = [str(value)[:10] for value in rows.get("max_source_result_date", pd.Series(dtype=object)).dropna() if str(value).strip()]
    max_history = max(max_dates) if max_dates else None
    validate_history_boundary(max_history, target_date)
    missing = [name for name in PRIMARY_NAMES if name not in rows]
    if missing:
        raise ForwardAdapterError(f"PRIMARY_SOURCE_FIELDS_MISSING:{','.join(missing)}")
    primary = rows.loc[:, PRIMARY_NAMES].copy()
    validate_exact_frame(primary, PRIMARY_NAMES, PRIMARY_HASH)
    head_values: dict[str, Any] = {}
    for name in RACE_HEAD_NAMES:
        values = primary[name]
        if name in RACE_HEAD_CATEGORICAL:
            normalized = values.fillna("__MISSING__").astype(str)
            if normalized.nunique(dropna=False) != 1:
                raise ForwardAdapterError(f"RACE_HEAD_NOT_CONSTANT:{name}")
            head_values[name] = normalized.iloc[0]
        else:
            numeric = pd.to_numeric(values, errors="coerce")
            finite = numeric.dropna()
            if len(finite) and float(finite.max() - finite.min()) > 1e-12:
                raise ForwardAdapterError(f"RACE_HEAD_NOT_CONSTANT:{name}")
            if len(finite) != len(numeric) and len(finite):
                raise ForwardAdapterError(f"RACE_HEAD_MIXED_MISSING:{name}")
            head_values[name] = float(finite.iloc[0]) if len(finite) else float("nan")
    race_head = pd.DataFrame([head_values], columns=RACE_HEAD_NAMES)
    validate_exact_frame(race_head, RACE_HEAD_NAMES, RACE_HEAD_HASH, newline_joined=True)
    return AdaptedRace(
        str(rows.loc[0, "race_key"]), target_date,
        tuple(rows["horse_number"].astype(int)), primary, race_head, max_history,
    )


def materialize_pre_race_safe_inputs(
    history_source: HistorySource,
    target_source: TargetRaceSource,
    *, race_key: str,
    target_date: str,
    mode: str,
    phase: str,
) -> AdaptedRace:
    if mode not in MODES or phase != "PHASE_B":
        raise ForwardAdapterError("PHASE_B_TARGET_MODE_REQUIRED")
    target = target_source.target_rows(race_key, target_date, mode)
    reject_outcome_fields(target.columns)
    validate_history_boundary(history_source.max_history_date, target_date)
    return adapt_materialized_rows(history_source.materialize(target, target_date))


def open_phase_b_live_history_source(*, phase: str, target_date: str, **kwargs: Any) -> Any:
    if phase != "PHASE_B":
        raise ForwardAdapterError("LIVE_HISTORY_PROVIDER_LOCKED_UNTIL_PHASE_B")
    from src.features.online.normalized_history_provider import P2NormalizedHistoricalAsOfProvider
    return P2NormalizedHistoricalAsOfProvider(target_date=target_date, **kwargs)
