from __future__ import annotations

import json
import os

import numpy as np
from catboost import CatBoostRegressor
from scipy.optimize import minimize, minimize_scalar


def main() -> None:
    imports = {"pass": True, "versions": {}}
    import catboost
    import pandas
    import scipy
    imports["versions"] = {"numpy": np.__version__, "scipy": scipy.__version__, "pandas": pandas.__version__, "catboost": catboost.__version__}
    x = np.array([[0.0, 1.0], [1.0, 0.0], [1.0, 1.0], [2.0, 1.0], [3.0, 2.0], [5.0, 3.0]], dtype=float)
    y = np.array([0.0, 0.8, 1.2, 1.9, 2.8, 4.7], dtype=float)
    params = {"task_type": "CPU", "loss_function": "RMSE", "iterations": 20, "depth": 3, "learning_rate": 0.05, "random_seed": 260904, "random_strength": 0, "bootstrap_type": "No", "thread_count": 1, "allow_writing_files": False, "verbose": False}
    one = CatBoostRegressor(**params).fit(x, y).predict(x)
    two = CatBoostRegressor(**params).fit(x, y).predict(x)
    max_abs = float(np.max(np.abs(one - two)))
    bounded = minimize_scalar(lambda v: (v - 1.25) ** 2 + 0.5, bounds=(-5.0, 5.0), method="bounded")
    lbfgsb = minimize(lambda a: (a[0] - 2.0) ** 2 + (a[1] + 1.0) ** 2, x0=np.array([0.0, 0.0]), method="L-BFGS-B")
    result = {"imports": imports, "catboost": {"pass": max_abs <= 1e-12, "max_abs_prediction_delta": max_abs, "tolerance": 1e-12, "thread_environment": {key: os.environ.get(key) for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}}, "scipy": {"pass": bool(bounded.success and lbfgsb.success), "bounded": {"success": bool(bounded.success), "x": float(bounded.x), "fun": float(bounded.fun)}, "l_bfgs_b": {"success": bool(lbfgsb.success), "x": [float(v) for v in lbfgsb.x], "fun": float(lbfgsb.fun)}}}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
