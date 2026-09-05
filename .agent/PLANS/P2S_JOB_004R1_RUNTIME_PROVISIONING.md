# P2S Job004R1 — Runtime Provisioning

## Inputs

- `data/manifests/successor_v1/JOB004_RUNTIME_PROVISIONING_FREEZE_V1.json`
- `/home/nabe/projects/nankan-market-residual-phase2/.venv-p2-model/bin/python`

## Outputs

- `audit/successor_v1/job004r1/` audit artifacts and immutable wheelhouse
- `data/manifests/successor_v1/RUNTIME_FREEZE_V1.json`

## Invariants

- Python 3.12.3; NumPy 2.5.2 and SciPy 1.18.0 remain unchanged.
- Add only binary wheels resolving pandas 3.0.5 and CatBoost 1.2.10.
- Network is restricted to pip provisioning from the two authorized PyPI hosts and is disabled after download.
- Synthetic smoke tests use no project data; no Job004 model work is performed.

## Acceptance checks

- Resolver proposes exact pins and no source artifacts.
- Wheel hashes are recorded before local-only installation.
- `pip check`, imports, deterministic CatBoost smoke test, and two SciPy optimizer smokes pass.
