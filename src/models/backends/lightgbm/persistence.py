"""Engineering-only model persistence parity."""
from __future__ import annotations

from pathlib import Path

import lightgbm


def save_and_reload(model, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(path))
    return lightgbm.Booster(model_file=str(path))
