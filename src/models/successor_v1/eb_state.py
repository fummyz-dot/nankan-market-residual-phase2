"""Amendment 006 reference EB backfit implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable

import numpy as np


LAYERS = ("horse", "jockey", "horse_x_venue", "jockey_x_venue")


@dataclass(frozen=True)
class BackfitResult:
    effects: dict[str, dict[Hashable, float]]
    components: dict[str, tuple[float, float]]
    cycles: int
    converged: bool
    final_max_abs_change: float
    initialized_from_zero: bool


def _lookup(mapping: dict[Hashable, float], keys: np.ndarray) -> np.ndarray:
    return np.fromiter((mapping.get(key, 0.0) for key in keys), dtype=np.float64, count=len(keys))


def _max_change(old: dict[Hashable, float], new: dict[Hashable, float]) -> float:
    keys = old.keys() | new.keys()
    return max((abs(old.get(key, 0.0) - new.get(key, 0.0)) for key in keys), default=0.0)


def _group_stats(values: np.ndarray, keys: np.ndarray, mask: np.ndarray) -> tuple[dict[Hashable, int], dict[Hashable, float]]:
    counts: dict[Hashable, int] = {}
    sums: dict[Hashable, float] = {}
    for value, key in zip(values[mask], keys[mask], strict=True):
        counts[key] = counts.get(key, 0) + 1
        sums[key] = sums.get(key, 0.0) + float(value)
    means = {key: sums[key] / counts[key] for key in counts}
    return counts, means


def _components(values: np.ndarray, keys: np.ndarray, mask: np.ndarray) -> tuple[float, float, dict[Hashable, int], dict[Hashable, float]]:
    used = values[mask]
    if len(used) == 0:
        return 0.0, 0.0, {}, {}
    sigma2 = float(np.mean(used * used, dtype=np.float64))
    counts, means = _group_stats(values, keys, mask)
    total = sum(counts.values())
    mu = sum(counts[key] * means[key] for key in counts) / total
    var_w = sum(counts[key] * (means[key] - mu) ** 2 for key in counts) / total
    e_inv_n = sum((counts[key] / total) * (1.0 / counts[key]) for key in counts)
    tau2 = max(0.0, float(var_w - sigma2 * e_inv_n))
    return sigma2, tau2, counts, means


def _raw_effects(counts: dict[Hashable, int], means: dict[Hashable, float], sigma2: float, tau2: float) -> dict[Hashable, float]:
    if tau2 == 0.0:
        return {key: 0.0 for key in counts}
    return {key: float(tau2 / (tau2 + sigma2 / count) * means[key]) for key, count in counts.items()}


def _center_interactions(raw: dict[tuple[str, str], float], counts: dict[tuple[str, str], int]) -> dict[tuple[str, str], float]:
    by_parent: dict[str, list[tuple[str, str]]] = {}
    for key in raw:
        by_parent.setdefault(key[0], []).append(key)
    centered: dict[tuple[str, str], float] = {}
    for parent, keys in by_parent.items():
        if len({key[1] for key in keys}) < 2:
            centered.update({key: 0.0 for key in keys})
            continue
        denominator = sum(counts[key] for key in keys)
        center = sum(counts[key] * raw[key] for key in keys) / denominator
        centered.update({key: float(raw[key] - center) for key in keys})
    return centered


def backfit(
    residual: np.ndarray,
    horse: np.ndarray,
    jockey: np.ndarray,
    venue: np.ndarray,
    *,
    mode: str,
    fixed_components: dict[str, tuple[float, float]] | None = None,
    max_cycles: int = 20,
    tolerance: float = 1e-5,
) -> BackfitResult:
    residual = np.asarray(residual, dtype=np.float64)
    horse = np.asarray(horse, dtype=object)
    jockey = np.asarray(jockey, dtype=object)
    venue = np.asarray(venue, dtype=object)
    if not (len(residual) == len(horse) == len(jockey) == len(venue)) or np.any(~np.isfinite(residual)):
        raise ValueError("invalid EB observations")
    if mode not in {"REESTIMATE", "FIXED_COMPONENT"} or (mode == "FIXED_COMPONENT" and set(fixed_components or {}) != set(LAYERS)):
        raise ValueError("invalid EB component mode")
    missing_jockey = np.fromiter((key is None or not str(key).strip() for key in jockey), dtype=bool, count=len(jockey))
    jockey = np.asarray([None if missing else str(key) for key, missing in zip(jockey, missing_jockey, strict=True)], dtype=object)
    hv = np.empty(len(horse), dtype=object)
    hv[:] = [(str(h), str(v)) for h, v in zip(horse, venue, strict=True)]
    jv = np.empty(len(jockey), dtype=object)
    jv[:] = [(str(j), str(v)) if j is not None else None for j, v in zip(jockey, venue, strict=True)]
    keys = {"horse": horse, "jockey": jockey, "horse_x_venue": hv, "jockey_x_venue": jv}
    effects: dict[str, dict[Hashable, float]] = {layer: {} for layer in LAYERS}
    final_components: dict[str, tuple[float, float]] = {}
    final_change = float("inf")
    converged = False
    for cycle in range(1, max_cycles + 1):
        cycle_change = 0.0
        for layer in LAYERS:
            adjusted = residual.copy()
            for other in LAYERS:
                if other != layer:
                    adjusted -= _lookup(effects[other], keys[other])
            mask = np.ones(len(residual), dtype=bool)
            if layer in {"jockey", "jockey_x_venue"}:
                mask &= ~missing_jockey
            if layer in {"horse_x_venue", "jockey_x_venue"}:
                parent = horse if layer == "horse_x_venue" else jockey
                venues_by_parent: dict[Hashable, set[str]] = {}
                for p, v, eligible in zip(parent, venue, mask, strict=True):
                    if eligible:
                        venues_by_parent.setdefault(p, set()).add(str(v))
                mask &= np.fromiter((p in venues_by_parent and len(venues_by_parent[p]) >= 2 for p in parent), dtype=bool, count=len(parent))
            estimated_sigma2, estimated_tau2, counts, means = _components(adjusted, keys[layer], mask)
            sigma2, tau2 = (estimated_sigma2, estimated_tau2) if mode == "REESTIMATE" else fixed_components[layer]  # type: ignore[index]
            if not np.isfinite(sigma2) or not np.isfinite(tau2) or sigma2 < 0 or tau2 < 0:
                raise ValueError("invalid EB variance component")
            raw = _raw_effects(counts, means, sigma2, tau2)
            updated = _center_interactions(raw, counts) if layer in {"horse_x_venue", "jockey_x_venue"} else raw
            if any(not np.isfinite(value) for value in updated.values()):
                raise ValueError("nonfinite EB effect")
            cycle_change = max(cycle_change, _max_change(effects[layer], updated))
            effects[layer] = updated
            final_components[layer] = (float(sigma2), float(tau2))
        final_change = cycle_change
        if cycle_change < tolerance:
            converged = True
            break
    return BackfitResult(effects, final_components, cycle, converged, final_change, True)


def score_effects(result: BackfitResult, horse: np.ndarray, jockey: np.ndarray, venue: np.ndarray) -> np.ndarray:
    values = np.zeros(len(horse), dtype=np.float64)
    for index, (h, j, v) in enumerate(zip(horse, jockey, venue, strict=True)):
        values[index] = result.effects["horse"].get(h, 0.0) + result.effects["horse_x_venue"].get((str(h), str(v)), 0.0)
        if j is not None and str(j).strip():
            values[index] += result.effects["jockey"].get(str(j), 0.0) + result.effects["jockey_x_venue"].get((str(j), str(v)), 0.0)
    return values
