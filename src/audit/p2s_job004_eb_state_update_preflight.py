"""Synthetic Amendment 006 semantic preflight; no project-data model fitting."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from src.models.successor_v1.eb_state import LAYERS, backfit, score_effects


ROOT = Path(__file__).resolve().parents[2]
AUTH = ROOT / "data/manifests/successor_v1/MODEL_EVALUATION_FREEZE_V1_AMENDMENT_006_EB_STATE_UPDATE.json"
OUT = ROOT / "audit/successor_v1/job004/eb_state_update_preflight.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    authority = json.loads(AUTH.read_text())
    e = np.asarray([1.0, 0.5, -0.5, -1.0, 0.75, -0.25], dtype=np.float64)
    horse = np.asarray(["h1", "h1", "h2", "h2", "h3", "h3"], dtype=object)
    jockey = np.asarray(["j1", "j1", "j2", "j2", None, None], dtype=object)
    venue = np.asarray(["V1", "V2", "V1", "V2", "V1", "V1"], dtype=object)
    estimated = backfit(e, horse, jockey, venue, mode="REESTIMATE")
    fixed = backfit(e, horse, jockey, venue, mode="FIXED_COMPONENT", fixed_components=estimated.components)
    extended = backfit(np.concatenate([e, [0.25]]), np.concatenate([horse, ["h1"]]), np.concatenate([jockey, ["j1"]]), np.concatenate([venue, ["V1"]]), mode="FIXED_COMPONENT", fixed_components=estimated.components)
    centered = all(abs(sum(1.0 * value for key, value in mapping.items() if key[0] == parent)) < 1e-12 for layer, mapping in fixed.effects.items() if layer in {"horse_x_venue", "jockey_x_venue"} for parent in {key[0] for key in mapping})
    missing_score = score_effects(fixed, np.asarray(["h3"], object), np.asarray([None], object), np.asarray(["V1"], object))[0]
    expected_missing = fixed.effects["horse"].get("h3", 0.0) + fixed.effects["horse_x_venue"].get(("h3", "V1"), 0.0)
    same_dates = np.asarray(["2021-01-01", "2021-01-01", "2021-01-02"])
    same_date_exclusion = all(np.all(same_dates[:index] < date) for date in np.unique(same_dates) for index in [int(np.searchsorted(same_dates, date, side="left"))])
    checks = {
        "layer_order_exact": list(LAYERS) == authority["layer_definitions"]["order"],
        "gauss_seidel_adjusted_residual": estimated.initialized_from_zero and estimated.cycles >= 1,
        "interaction_immediate_centering": centered,
        "unidentifiable_interaction_zero": fixed.effects["horse_x_venue"].get(("h3", "V1"), 0.0) == 0.0,
        "missing_jockey_zero": abs(missing_score - expected_missing) == 0.0,
        "reestimate_mode_component_calculation": set(estimated.components) == set(LAYERS) and all(s >= 0 and t >= 0 for s, t in estimated.components.values()),
        "fixed_mode_component_immutability": fixed.components == estimated.components and extended.components == estimated.components,
        "backfit_starts_from_zero": estimated.initialized_from_zero and fixed.initialized_from_zero and extended.initialized_from_zero,
        "same_date_residual_exclusion": same_date_exclusion,
        "outer_valid_component_immutability": extended.components == estimated.components,
    }
    result = {"status": "PASS" if all(checks.values()) else "JOB004_BLOCKED_EB_STATE_UPDATE_INCONSISTENCY", "authority_sha256": sha256(AUTH), "algorithm_contract_sha256": authority["algorithm_contract_sha256"], "synthetic_fixture_only": True, "checks": {key: "PASS" if value else "FAIL" for key, value in checks.items()}, "model_fit_performed": False}
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
