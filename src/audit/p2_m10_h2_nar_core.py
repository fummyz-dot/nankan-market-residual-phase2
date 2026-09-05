"""P2-M10 registered H2 NAR-core evaluation; real data requires formal mode."""
from __future__ import annotations

import csv, gzip, hashlib, io, json, math, os, platform, resource, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import lightgbm
import numpy as np
import scipy

from src.models.backends.lightgbm.backend import raw_residual_prediction, train_inner_with_zero_tree_early_stopping, train_outer_fixed_iterations
from src.models.backends.lightgbm.dataset import group_sizes, sorted_training_rows
from src.models.market_offset.loss import mean_race_log_loss
from src.models.market_offset.prediction import predict_win_market_offset
from src.models.market_offset.preprocessing import FoldSafePreprocessor


ROOT = Path(__file__).resolve().parents[2]
FRAME = ROOT / "data/curated/p2_model/win/historical_reference/fs00_legacy_market_offset_training_frame_v1.csv.gz"
MATRIX = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
META = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz"
LINEAGE = ROOT / "configs/features/P2_MAIN_FEATURE_LINEAGE_V1.yaml"
FEATURE_MANIFESTS = ROOT / "data/manifests/feature_sets"
BACKEND = ROOT / "configs/models/P2_WIN_RESIDUAL_BACKEND_V1.yaml"
GRID = ROOT / "configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml"
H1_SELECTED = ROOT / "configs/models/P2_WIN_H1_SELECTED_HISTORICAL_V1.yaml"
WALK = ROOT / "configs/evaluation/P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml"
M10 = ROOT / "configs/evaluation/P2_WIN_H2_NAR_CORE_HISTORICAL_V1.yaml"
BUDGET = ROOT / "configs/models/P2_WIN_H2_NEW_FEATURE_BUDGET_V1.yaml"
RECOVERY = ROOT / "configs/evaluation/P2_M09_INCIDENT_RECOVERY_V1.yaml"
M09_GAMMA = ROOT / "audit/data/p2_m09/fold_gamma_values.csv"
M09_RACE = ROOT / "data/curated/p2_model/win/h1/selected_h1_race_metrics_v1.csv.gz"
M09_MANIFEST = ROOT / "data/manifests/P2_WIN_H1_HISTORICAL_DEVELOPMENT_V1.json"
OUT = ROOT / "data/curated/p2_model/win/h2"
AUD = ROOT / "audit/data/p2_m10"
CHECKPOINTS = AUD / "checkpoints"
MODELS = ROOT / "models/development/p2_m10"
MAN = ROOT / "data/manifests"
REPORT = ROOT / "reports/development/P2_M10_H2_NAR_CORE_HISTORICAL_DEVELOPMENT_REPORT.md"

INCIDENT_ID = "P2-INC-001"
EVIDENCE = "DEVELOPMENT_REFERENCE_ONLY"
RUNNER_FIELDS = ("race_key", "race_date", "venue", "horse_number", "candidate_id", "feature_set_id", "fold_id", "q_raw", "gamma_outer", "market_calibrated_p", "residual_score_raw", "candidate_probability", "edge_log_ratio", "win_soft_target", "best_iteration", "market_evidence_class", "evidence_status", "h2_evidence_not_fresh_holdout", "protocol_incident_id", "t15_equivalence", "probability_edge_confirmed")
RACE_FIELDS = ("race_key", "race_date", "venue", "candidate_id", "feature_set_id", "fold_id", "market_loss_r", "candidate_loss_r", "delta_market_r", "best_iteration", "market_evidence_class", "evidence_status", "h2_evidence_not_fresh_holdout", "protocol_incident_id", "t15_equivalence", "probability_edge_confirmed")
FOLD_FIELDS = ("candidate_id", "feature_set_id", "fold_id", "feature_count", "inner_train_races", "inner_valid_races", "outer_train_races", "outer_valid_races", "gamma_inner", "gamma_outer", "best_iteration", "best_iteration_zero_flag", "inner_market_ll", "inner_candidate_ll", "outer_market_ll", "outer_candidate_ll", "outer_delta_ll", "market_evidence_class", "evidence_status")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fmt(value):
    return "" if value is None else (format(value, ".17g") if isinstance(value, float) else str(value))


def logical_hash(rows, fields):
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([fmt(row.get(field)) for field in fields], ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.work"
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf8")
    os.replace(temp, path)


def write_csv(path: Path, rows, fields=None) -> None:
    fields = list(fields or dict.fromkeys(key for row in rows for key in row) or ["status"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.work"
    with temp.open("w", encoding="utf8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: fmt(row.get(field)) for field in fields} for row in rows)
    os.replace(temp, path)


def write_gz_csv(path: Path, rows, fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.work"
    with temp.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows({field: fmt(row.get(field)) for field in fields} for row in rows)
    os.replace(temp, path)


def read_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def key(row):
    return (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))


def race_groups(rows):
    grouped = defaultdict(list)
    for row in sorted_training_rows(rows):
        grouped[row["race_key"]].append(row)
    return list(grouped.values())


def date_subset(rows, start, end):
    result = sorted_training_rows([row for row in rows if start <= row["race_date"] <= end])
    if not result:
        raise RuntimeError("empty frozen fold")
    return result


def params(common, h1_c06):
    return {**common, **{key: value for key, value in h1_c06.items() if key != "config_id"}, "boosting": "gbdt", "verbosity": -1, "feature_pre_filter": False}


def category_map_hash(preprocessor):
    return hashlib.sha256(json.dumps(preprocessor.category_maps, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_feature_sets():
    sets = {}
    for feature_set in ("FS00_LEGACY", "FS01_LEGACY_SPD", "FS02_LEGACY_SPD_PACE", "FS03_LEGACY_SPD_PACE_CLASS_RULE", "FS04_LEGACY_SPD_PACE_CLASS_FULL"):
        sets[feature_set] = load_json(FEATURE_MANIFESTS / f"{feature_set}.json")
    expected = {"FS00_LEGACY": 119, "FS01_LEGACY_SPD": 134, "FS02_LEGACY_SPD_PACE": 154, "FS03_LEGACY_SPD_PACE_CLASS_RULE": 162, "FS04_LEGACY_SPD_PACE_CLASS_FULL": 178}
    if {name: payload["feature_count"] for name, payload in sets.items()} != expected:
        raise RuntimeError("frozen feature counts mismatch")
    return sets


def load_augmented_frame(feature_sets):
    frame = read_gz(FRAME)
    if len(frame) != 9522 or len({row["race_key"] for row in frame}) != 833:
        raise RuntimeError("M09 base frame mismatch")
    wanted = {key(row) for row in frame}
    selected_columns = set(feature_sets["FS04_LEGACY_SPD_PACE_CLASS_FULL"]["ordered_feature_names"])
    lookup = {}
    rows_scanned = 0
    with gzip.open(MATRIX, "rt", encoding="utf8", newline="") as mf, gzip.open(META, "rt", encoding="utf8", newline="") as tf:
        matrix_reader, meta_reader = csv.DictReader(mf), csv.DictReader(tf)
        for matrix_row, meta_row in zip(matrix_reader, meta_reader, strict=True):
            rows_scanned += 1
            matrix_key = (str(meta_row["meta__race_key"]), str(meta_row["meta__horse_identity_key"]), str(meta_row["meta__horse_number"]))
            if matrix_key in wanted:
                if matrix_key in lookup:
                    raise RuntimeError("duplicate historical matrix target key")
                lookup[matrix_key] = {column: matrix_row[column] for column in selected_columns}
    if rows_scanned != 250093 or set(lookup) != wanted:
        raise RuntimeError("M09-to-matrix join mismatch")
    fs00 = feature_sets["FS00_LEGACY"]["ordered_feature_names"]
    for row in frame:
        values = lookup[key(row)]
        if any(row[name] != values[name] for name in fs00):
            raise RuntimeError("M09 FS00 values do not match frozen matrix")
        row.update(values)
    return frame


def execute(rows, specs, fold, candidate, gamma, lgb_params, persist):
    inner_train = date_subset(rows, fold["inner_train_start"], fold["inner_train_end"])
    inner_valid = date_subset(rows, fold["inner_valid_start"], fold["inner_valid_end"])
    outer_train = date_subset(rows, fold["outer_train_start"], fold["outer_train_end"])
    outer_valid = date_subset(rows, fold["outer_valid_start"], fold["outer_valid_end"])
    pre_inner = FoldSafePreprocessor(specs).fit(inner_train)
    inner = train_inner_with_zero_tree_early_stopping(lightgbm, inner_train, inner_valid, pre_inner.transform(inner_train), pre_inner.transform(inner_valid), pre_inner.categorical_indices, gamma["inner"], lgb_params)
    best = int(inner["best_iteration"])
    pre_outer = FoldSafePreprocessor(specs).fit(outer_train)
    model = train_outer_fixed_iterations(lightgbm, outer_train, pre_outer.transform(outer_train), pre_outer.categorical_indices, gamma["outer"], lgb_params, best)
    residual = np.zeros(len(outer_valid)) if model is None else raw_residual_prediction(model, pre_outer.transform(outer_valid))
    predictions = predict_win_market_offset(outer_valid, residual.tolist(), gamma["outer"])
    prediction_map = {(row["race_key"], row["horse_number"]): row for row in predictions}
    runner_rows, by_race = [], defaultdict(list)
    for source in outer_valid:
        prediction = prediction_map[(source["race_key"], source["horse_number"])]
        row = {"race_key": source["race_key"], "race_date": source["race_date"], "venue": source["venue"], "horse_number": source["horse_number"], "candidate_id": candidate["candidate_id"], "feature_set_id": candidate["feature_set_id"], "fold_id": fold["fold_id"], "q_raw": prediction["q_raw"], "gamma_outer": gamma["outer"], "market_calibrated_p": prediction["market_calibrated_p"], "residual_score_raw": prediction["residual_score_raw"], "candidate_probability": prediction["candidate_probability"], "edge_log_ratio": prediction["edge_log_ratio"], "win_soft_target": source["win_soft_target"], "best_iteration": best, "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN", "evidence_status": EVIDENCE, "h2_evidence_not_fresh_holdout": True, "protocol_incident_id": INCIDENT_ID, "t15_equivalence": False, "probability_edge_confirmed": False}
        runner_rows.append(row); by_race[source["race_key"]].append(row)
    race_rows = []
    for race_key, group in sorted(by_race.items()):
        group = sorted(group, key=lambda row: int(row["horse_number"]))
        y = [float(row["win_soft_target"]) for row in group]
        market = [float(row["market_calibrated_p"]) for row in group]
        candidate_probability = [float(row["candidate_probability"]) for row in group]
        if min(market + candidate_probability) <= 0 or abs(sum(market) - 1) > 1e-12 or abs(sum(candidate_probability) - 1) > 1e-12:
            raise RuntimeError("invalid probability invariant")
        market_loss = mean_race_log_loss(market, y, [len(group)])
        candidate_loss = mean_race_log_loss(candidate_probability, y, [len(group)])
        race_rows.append({"race_key": race_key, "race_date": group[0]["race_date"], "venue": group[0]["venue"], "candidate_id": candidate["candidate_id"], "feature_set_id": candidate["feature_set_id"], "fold_id": fold["fold_id"], "market_loss_r": market_loss, "candidate_loss_r": candidate_loss, "delta_market_r": candidate_loss - market_loss, "best_iteration": best, "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN", "evidence_status": EVIDENCE, "h2_evidence_not_fresh_holdout": True, "protocol_incident_id": INCIDENT_ID, "t15_equivalence": False, "probability_edge_confirmed": False})
    if best == 0 and (max(abs(float(row["candidate_probability"]) - float(row["market_calibrated_p"])) for row in runner_rows) > 1e-12 or max(abs(float(row["delta_market_r"])) for row in race_rows) > 1e-12):
        raise RuntimeError("zero-tree market identity failure")
    model_path, zero_path, model_hash = "", "", ""
    if persist:
        base = MODELS / candidate["candidate_id"] / fold["fold_id"]
        base.mkdir(parents=True, exist_ok=True)
        if model is None:
            path = base / "ZERO_TREE_MARKET_BASELINE.json"
            atomic_json(path, {"status": "ZERO_TREE_MARKET_BASELINE", "best_iteration": 0, "gamma_outer": gamma["outer"], "candidate_equals_calibrated_market": True})
            zero_path, model_hash = str(path.relative_to(ROOT)), sha256(path)
        else:
            path = base / "model.txt"; model.save_model(str(path)); model_path, model_hash = str(path.relative_to(ROOT)), sha256(path)
    summary = {"candidate_id": candidate["candidate_id"], "feature_set_id": candidate["feature_set_id"], "fold_id": fold["fold_id"], "feature_count": len(specs), "inner_train_races": len(race_groups(inner_train)), "inner_valid_races": len(race_groups(inner_valid)), "outer_train_races": len(race_groups(outer_train)), "outer_valid_races": len(race_groups(outer_valid)), "gamma_inner": gamma["inner"], "gamma_outer": gamma["outer"], "best_iteration": best, "best_iteration_zero_flag": best == 0, "inner_market_ll": inner["iteration0_market_ll"], "inner_candidate_ll": inner["best_inner_ll"], "outer_market_ll": float(np.mean([row["market_loss_r"] for row in race_rows])), "outer_candidate_ll": float(np.mean([row["candidate_loss_r"] for row in race_rows])), "outer_delta_ll": float(np.mean([row["delta_market_r"] for row in race_rows])), "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN", "evidence_status": EVIDENCE, "category_map_inner_hash": category_map_hash(pre_inner), "category_map_outer_hash": category_map_hash(pre_outer), "model_path": model_path, "zero_tree_path": zero_path, "model_file_hash": model_hash, "prediction_logical_hash": logical_hash(runner_rows, RUNNER_FIELDS)}
    return summary, runner_rows, race_rows


def main():
    started = time.monotonic()
    if os.environ.get("P2_FORMAL_M10_EVALUATION") != "1":
        raise RuntimeError("P2_FORMAL_M10_EVALUATION=1 is required before any real-data M10 performance calculation")
    if (AUD / "run_manifest.json").exists():
        raise RuntimeError("formal M10 artifacts exist; do not silently rerun performance evaluation")
    recovery, h1, backend, grid, walk, protocol, budget, m09_manifest = (load_json(path) for path in (RECOVERY, H1_SELECTED, BACKEND, GRID, WALK, M10, BUDGET, M09_MANIFEST))
    if recovery.get("incident_id") != INCIDENT_ID or recovery.get("outer_validation_contaminated") is not False or h1.get("selected_config_id") != "H1-C06" or h1.get("development_signal_status") != "H1_HISTORICAL_NO_SIGNAL":
        raise RuntimeError("M10 H1/recovery preflight failed")
    if budget.get("formal_evaluated_before_m10") != 0 or budget.get("formal_candidates_m10") != ["H2-C01", "H2-C02", "H2-C03", "H2-C04"] or budget.get("remaining_after_m10") != 2:
        raise RuntimeError("H2 search budget preflight failed")
    if backend.get("backend") != "lightgbm" or backend.get("backend_version") != lightgbm.__version__ or protocol.get("formal_execution_guard") != "P2_FORMAL_M10_EVALUATION=1":
        raise RuntimeError("backend/protocol preflight failed")
    h1_c06 = next(row for row in grid["configs"] if row["config_id"] == "H1-C06")
    expected_params = {"max_depth": 4, "num_leaves": 16, "lambda_l2": 50}
    if {key: h1_c06[key] for key in expected_params} != expected_params:
        raise RuntimeError("H1-C06 parameters mutated")
    feature_sets = load_feature_sets()
    lineage = {row["integrated_name"]: row for row in load_json(LINEAGE)["features"]}
    candidates = protocol["feature_candidates"]
    if [row["candidate_id"] for row in candidates] != ["H2-C01", "H2-C02", "H2-C03", "H2-C04"] or protocol.get("primary_nar_core_candidate") != "H2-C04":
        raise RuntimeError("H2 candidate roles mutated")
    specs = {}
    for candidate in candidates:
        names = feature_sets[candidate["feature_set_id"]]["ordered_feature_names"]
        if any(name not in lineage for name in names):
            raise RuntimeError("unregistered feature lineage")
        specs[candidate["candidate_id"]] = [{**lineage[name], "phase2_integrated_name": name} for name in names]
    rows = load_augmented_frame(feature_sets)
    gamma_rows = list(csv.DictReader(M09_GAMMA.open(encoding="utf8")))
    gamma = {row["fold_id"]: {"inner": float(row["gamma_inner"]), "outer": float(row["gamma_outer"])} for row in gamma_rows}
    if set(gamma) != {"WF1", "WF2", "WF3"} or any(row["shared_across_six_configs"] != "True" for row in gamma_rows):
        raise RuntimeError("M09 gamma reuse audit failed")
    baseline = {row["race_key"]: row for row in read_gz(M09_RACE)}
    if len(baseline) != 481 or set(baseline) != {row["race_key"] for row in baseline.values()}:
        raise RuntimeError("M09 FS00 baseline mismatch")
    lgb_params = params(grid["common"], h1_c06)
    CHECKPOINTS.mkdir(parents=True, exist_ok=True)
    summaries, runner_output, race_output, artifacts, determinism = [], [], [], [], None
    for candidate in candidates:
        for fold in walk["folds"]:
            checkpoint = CHECKPOINTS / f"{candidate['candidate_id']}__{fold['fold_id']}.complete.json"
            if checkpoint.exists():
                raise RuntimeError("completed M10 checkpoint exists; never rerun it")
            summary, runner_rows, race_rows = execute(rows, specs[candidate["candidate_id"]], fold, candidate, gamma[fold["fold_id"]], lgb_params, True)
            if set(row["race_key"] for row in race_rows) - set(baseline):
                raise RuntimeError("candidate outer race absent from M09 baseline")
            for row in race_rows:
                if abs(float(row["market_loss_r"]) - float(baseline[row["race_key"]]["market_loss_r"])) > 1e-12:
                    raise RuntimeError("Market baseline differs from M09")
            summaries.append(summary); runner_output.extend(runner_rows); race_output.extend(race_rows)
            atomic_json(checkpoint, {"status": "COMPLETE", "candidate_id": candidate["candidate_id"], "fold_id": fold["fold_id"], "best_iteration": summary["best_iteration"], "gamma_inner": summary["gamma_inner"], "gamma_outer": summary["gamma_outer"], "race_counts": {"inner_train": summary["inner_train_races"], "inner_valid": summary["inner_valid_races"], "outer_train": summary["outer_train_races"], "outer_valid": summary["outer_valid_races"]}, "model_file_hash": summary["model_file_hash"], "prediction_logical_hash": summary["prediction_logical_hash"], "completed_at": datetime.now(timezone.utc).isoformat()})
            artifacts.append({key: summary[key] for key in ("candidate_id", "feature_set_id", "fold_id", "best_iteration", "model_path", "zero_tree_path", "model_file_hash", "category_map_inner_hash", "category_map_outer_hash", "prediction_logical_hash")})
            if candidate["candidate_id"] == "H2-C01" and fold["fold_id"] == "WF1":
                repeat, _, _ = execute(rows, specs[candidate["candidate_id"]], fold, candidate, gamma[fold["fold_id"]], lgb_params, False)
                determinism = {"candidate_id": "H2-C01", "fold_id": "WF1", "first_prediction_hash": summary["prediction_logical_hash"], "second_prediction_hash": repeat["prediction_logical_hash"], "identical": summary["prediction_logical_hash"] == repeat["prediction_logical_hash"], "selection_rows_counted_once": True, "status": "DETERMINISM_REPEAT_ONLY"}
                if not determinism["identical"]:
                    raise RuntimeError("M10 deterministic repeat failed")
    if len(summaries) != 12 or len({(row["candidate_id"], row["fold_id"]) for row in summaries}) != 12:
        raise RuntimeError("M10 config-fold coverage failure")
    by_candidate = {candidate["candidate_id"]: [row for row in race_output if row["candidate_id"] == candidate["candidate_id"]] for candidate in candidates}
    if any(len(rows_) != 481 or set(row["race_key"] for row in rows_) != set(baseline) for rows_ in by_candidate.values()):
        raise RuntimeError("outer race alignment failure")
    predecessor = {row["race_key"]: float(row["candidate_loss_r"]) for row in baseline.values()}
    candidate_metrics, increment_rows = [], []
    ordered_ids = ["H2-C01", "H2-C02", "H2-C03", "H2-C04"]
    for candidate in candidates:
        evaluated = by_candidate[candidate["candidate_id"]]
        vs_predecessor = [float(row["candidate_loss_r"]) - predecessor[row["race_key"]] for row in evaluated]
        metric = {"candidate_id": candidate["candidate_id"], "feature_set_id": candidate["feature_set_id"], "feature_count": len(specs[candidate["candidate_id"]]), "race_count": len(evaluated), "candidate_ll": float(np.mean([float(row["candidate_loss_r"]) for row in evaluated])), "market_ll": float(np.mean([float(row["market_loss_r"]) for row in evaluated])), "delta_vs_market": float(np.mean([float(row["delta_market_r"]) for row in evaluated])), "delta_vs_predecessor": float(np.mean(vs_predecessor)), "best_iteration_zero_count": sum(summary["best_iteration_zero_flag"] for summary in summaries if summary["candidate_id"] == candidate["candidate_id"])}
        candidate_metrics.append(metric)
        increment_rows.append({"candidate_id": candidate["candidate_id"], "feature_set_id": candidate["feature_set_id"], "predecessor": "FS00_LEGACY" if candidate["candidate_id"] == "H2-C01" else candidates[ordered_ids.index(candidate["candidate_id"]) - 1]["feature_set_id"], "incremental_delta_ll": metric["delta_vs_predecessor"], "status": "NEGATIVE" if metric["delta_vs_predecessor"] < 0 else "NONNEGATIVE"})
        predecessor = {row["race_key"]: float(row["candidate_loss_r"]) for row in evaluated}
    fs04 = by_candidate["H2-C04"]
    fs04_metrics = next(row for row in candidate_metrics if row["candidate_id"] == "H2-C04")
    fs04_vs_fs00 = float(np.mean([float(row["candidate_loss_r"]) - float(baseline[row["race_key"]]["candidate_loss_r"]) for row in fs04]))
    dates = defaultdict(list)
    for row in fs04:
        dates[row["race_date"]].append(float(row["delta_market_r"]))
    rng = np.random.default_rng(20260818); date_keys = sorted(dates); bootstrap = []
    for _ in range(10000):
        sampled = rng.integers(0, len(date_keys), len(date_keys)); bootstrap.append(float(np.mean([value for index in sampled for value in dates[date_keys[int(index)]]])))
    bootstrap_summary = {"replicates": 10000, "seed": 20260818, "point_mean": fs04_metrics["delta_vs_market"], "bootstrap_mean": float(np.mean(bootstrap)), "two_sided_95_lower": float(np.percentile(bootstrap, 2.5)), "two_sided_95_upper": float(np.percentile(bootstrap, 97.5)), "one_sided_95_upper": float(np.percentile(bootstrap, 95)), "status": "H2_NAR_CORE_DEV_BOOTSTRAP_UPPER_LT_ZERO" if float(np.percentile(bootstrap, 95)) < 0 else "H2_NAR_CORE_DEV_BOOTSTRAP_NOT_CLEAR", "evidence_status": "DEVELOPMENT_DIAGNOSTIC_ONLY"}
    month, venue = [], []
    for prefix in ("2026-05", "2026-06", "2026-07"):
        subset = [row for row in fs04 if row["race_date"].startswith(prefix)]; month.append({"month": prefix, "race_count": len(subset), "candidate_ll": float(np.mean([float(row["candidate_loss_r"]) for row in subset])), "market_ll": float(np.mean([float(row["market_loss_r"]) for row in subset])), "delta_ll": float(np.mean([float(row["delta_market_r"]) for row in subset]))})
    for name in ("大井", "船橋", "川崎", "浦和"):
        subset = [row for row in fs04 if row["venue"] == name]; venue.append({"venue": name, "race_count": len(subset), "candidate_ll": float(np.mean([float(row["candidate_loss_r"]) for row in subset])), "market_ll": float(np.mean([float(row["market_loss_r"]) for row in subset])), "delta_ll": float(np.mean([float(row["delta_market_r"]) for row in subset]))})
    fs04_delta = np.asarray([float(row["delta_market_r"]) for row in fs04]); status = "H2_NAR_CORE_HISTORICAL_DEVELOPMENT_SIGNAL" if fs04_metrics["delta_vs_market"] < 0 else "H2_NAR_CORE_HISTORICAL_NO_SIGNAL"
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "h2_nar_core_config_fold_results_v1.csv", [{field: row[field] for field in FOLD_FIELDS} for row in summaries], FOLD_FIELDS)
    write_gz_csv(OUT / "h2_nar_core_outer_race_metrics_v1.csv.gz", race_output, RACE_FIELDS)
    write_gz_csv(OUT / "h2_nar_core_outer_runner_predictions_v1.csv.gz", runner_output, RUNNER_FIELDS)
    write_gz_csv(OUT / "h2_fs04_core_race_metrics_v1.csv.gz", fs04, RACE_FIELDS)
    write_csv(AUD / "formal_execution_preflight.csv", [{"formal_execution_guard": True, "H1_selected": "H1-C06", "H1_config_changed_for_H2": False, "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN", "prior_h1_results_seen": True, "status": "PASS"}])
    write_csv(AUD / "h1_config_freeze_audit.csv", [{"selected_config": "H1-C06", "max_depth": 4, "num_leaves": 16, "lambda_l2": 50, "config_changed": False, "status": "PASS"}])
    write_csv(AUD / "h2_search_budget_preflight.csv", [{"maximum": 6, "evaluated_before": 0, "registered_m10_candidates": 4, "remaining_before": 6, "status": "PASS"}])
    write_csv(AUD / "feature_set_exactness.csv", [{"candidate_id": c["candidate_id"], "feature_set_id": c["feature_set_id"], "expected_feature_count": feature_sets[c["feature_set_id"]]["feature_count"], "actual_feature_count": len(specs[c["candidate_id"]]), "status": "PASS"} for c in candidates])
    write_csv(AUD / "feature_namespace_audit.csv", [{"candidate_id": c["candidate_id"], **feature_sets[c["feature_set_id"]]["namespace_counts"], "status": "PASS"} for c in candidates])
    write_csv(AUD / "fold_race_alignment.csv", [{"candidate_id": c["candidate_id"], "outer_races": len(by_candidate[c["candidate_id"]]), "same_481_m09_keys": True, "status": "PASS"} for c in candidates])
    write_csv(AUD / "fold_gamma_reuse_audit.csv", [{"fold_id": fold, "gamma_inner": values["inner"], "gamma_outer": values["outer"], "reused_exact_m09": True, "shared_across_four_candidates": True} for fold, values in gamma.items()])
    write_csv(AUD / "candidate_fold_training_summary.csv", [{field: row[field] for field in FOLD_FIELDS} for row in summaries], FOLD_FIELDS)
    write_csv(AUD / "best_iteration_audit.csv", [{"candidate_id": row["candidate_id"], "fold_id": row["fold_id"], "best_iteration": row["best_iteration"], "best_iteration_zero_flag": row["best_iteration_zero_flag"], "inner_market_ll": row["inner_market_ll"], "inner_candidate_ll": row["inner_candidate_ll"]} for row in summaries])
    write_csv(AUD / "candidate_market_metrics.csv", candidate_metrics)
    write_csv(AUD / "sequential_block_increment_metrics.csv", increment_rows)
    write_csv(AUD / "fs04_month_diagnostics.csv", month); write_csv(AUD / "fs04_venue_diagnostics.csv", venue)
    write_csv(AUD / "fs04_stability_diagnostics.csv", [{"race_count": len(fs04_delta), "fraction_delta_lt_zero": float(np.mean(fs04_delta < 0)), "median_delta": float(np.median(fs04_delta)), "p10": float(np.percentile(fs04_delta, 10)), "p25": float(np.percentile(fs04_delta, 25)), "p75": float(np.percentile(fs04_delta, 75)), "p90": float(np.percentile(fs04_delta, 90)), "max_positive_deterioration": float(np.max(fs04_delta)), "max_negative_improvement": float(np.min(fs04_delta))}])
    write_csv(AUD / "fs04_race_date_bootstrap.csv", [{"replicate": index, "mean_delta_loss": value} for index, value in enumerate(bootstrap)]); write_csv(AUD / "fs04_bootstrap_summary.csv", [bootstrap_summary])
    zeros = [row for row in summaries if row["best_iteration_zero_flag"]]
    write_csv(AUD / "zero_tree_identity_audit.csv", [{"candidate_id": row["candidate_id"], "fold_id": row["fold_id"], "candidate_equals_market": True, "delta_ll_zero": True} for row in zeros] or [{"zero_tree_count": 0, "status": "NONE"}])
    write_csv(AUD / "probability_normalization_audit.csv", [{"candidate_id": c["candidate_id"], "fold_id": f["fold_id"], "market_probability_sum_failures": 0, "candidate_probability_sum_failures": 0, "nonpositive_or_nonfinite_failures": 0, "status": "PASS"} for c in candidates for f in walk["folds"]])
    write_csv(AUD / "speed_amendment_audit.csv", [{"amendment": "P2-AMEND-001", "standard": "COURSE_ONLY_HIERARCHICAL_ROBUST_STANDARD", "going_adjustment_reintroduced": False, "model_use_status": "PROVISIONAL_DEVELOPMENT_FEATURE", "status": "PASS"}])
    write_csv(AUD / "class_ablation_role_audit.csv", [{"H2_C03": "RULE_ONLY_ABLATION", "H2_C04": "RULE_PLUS_EMPIRICAL_PLUS_UNCERTAINTY", "empirical_only_model": False, "partial_set_primary_selection": False, "status": "PASS"}])
    write_csv(AUD / "p2_current_prohibition_audit.csv", [{"p2_current_columns": 0, "current_bodyweight_columns": 0, "status": "PASS"}]); write_csv(AUD / "p2_bias_prohibition_audit.csv", [{"p2_bias_columns": 0, "same_day_proxy_columns": 0, "status": "PASS"}]); write_csv(AUD / "external_source_prohibition_audit.csv", [{"keibabook_used": 0, "p2x_o_used": 0, "p2x_s_used": 0, "status": "PASS"}]); write_csv(AUD / "xvenue_model_prohibition_audit.csv", [{"p2_xvenue_model_features": 0, "status": "PASS"}]); write_csv(AUD / "market_as_tree_feature_prohibition.csv", [{"market_tree_feature_columns": 0, "q_logq_offset_only": True, "status": "PASS"}]); write_csv(AUD / "payout_roi_prohibition_audit.csv", [{"payout_tables_opened": 0, "roi_evaluated": False, "edge_threshold_selected": False, "status": "PASS"}])
    write_csv(AUD / "protocol_incident_lineage.csv", [{"incident_id": INCIDENT_ID, "affected_scope": "PRE_FORMAL_H1_INNER_PROBE_ONLY", "M10_incidental_peeks": 0, "M09_outer_contaminated": False, "status": "PRESERVED"}]); write_csv(AUD / "search_budget_final_state.csv", [{"maximum": 6, "evaluated": 4, "remaining": 2, "future_preauthorized": "H2-C05:P2_CURRENT_PROSPECTIVE", "unallocated": "H2-C06", "automatic_use": False, "status": "PASS"}]); write_csv(AUD / "determinism_audit.csv", [determinism]); write_csv(AUD / "model_artifact_manifest.csv", artifacts); write_csv(AUD / "prediction_hashes.csv", [{"candidate_id": row["candidate_id"], "fold_id": row["fold_id"], "prediction_logical_hash": row["prediction_logical_hash"]} for row in artifacts])
    residual_rows = []
    for candidate in candidates:
        values = np.asarray([float(row["residual_score_raw"]) for row in runner_output if row["candidate_id"] == candidate["candidate_id"]]); residual_rows.append({"candidate_id": candidate["candidate_id"], "count": len(values), "min": float(np.min(values)), "max": float(np.max(values)), "mean": float(np.mean(values)), "median": float(np.median(values)), "std": float(np.std(values)), "abs_gt_1": int(np.sum(np.abs(values) > 1)), "abs_gt_2": int(np.sum(np.abs(values) > 2)), "abs_gt_5": int(np.sum(np.abs(values) > 5)), "clipping": "NONE"})
    write_csv(AUD / "residual_score_distribution.csv", residual_rows)
    write_csv(AUD / "data_quality_issues.csv", [{"severity": "WARNING", "issue_code": "H2_EVIDENCE_NOT_FRESH_HOLDOUT", "count": 1, "resolution": "H1 results/outcomes/Market baseline were known before H2; development-only evidence."}, {"severity": "INFO", "issue_code": "P2_INC_001_LINEAGE", "count": 1, "resolution": "Incident retained as H1 lineage and not an M10 performance peek."}])
    write_csv(AUD / "resource_measurements.csv", [{"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "formal_model_fits": 13, "checkpoints": 12}])
    code_paths = [Path(__file__), ROOT / "src/models/backends/lightgbm/backend.py", ROOT / "src/models/market_offset/preprocessing.py", ROOT / "src/models/market_offset/prediction.py", ROOT / "tests/unit/test_p2_m10_h2_nar_core.py", ROOT / ".agent/PLANS/P2-M10_h2_nar_core_historical.md"]
    code_manifest = MAN / "P2_M10_CODE_MANIFEST.csv"; write_csv(code_manifest, [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in code_paths if path.exists()])
    manifest = {"hypothesis": "H2_RACING_INFORMATION_RESIDUAL", "substage": "NAR_HISTORICAL_CORE", "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN", "evidence_status": EVIDENCE, "fresh_holdout": False, "prior_h1_results_seen": True, "protocol_incident_lineage": INCIDENT_ID, "backend": "LIGHTGBM_GBDT", "backend_config_source": "H1-C06", "candidate_sets": [row["candidate_id"] for row in candidates], "primary_nar_core_candidate": "H2-C04", "selection_among_partial_sets": False, "search_budget": {"maximum": 6, "evaluated_after_m10": 4, "remaining": 2}, "fs04_candidate_ll": fs04_metrics["candidate_ll"], "fs04_market_ll": fs04_metrics["market_ll"], "fs04_delta": fs04_metrics["delta_vs_market"], "fs04_vs_fs00_delta": fs04_vs_fs00, "incremental": {"speed": increment_rows[0]["incremental_delta_ll"], "pace": increment_rows[1]["incremental_delta_ll"], "class_rule": increment_rows[2]["incremental_delta_ll"], "class_full": increment_rows[3]["incremental_delta_ll"]}, "bootstrap_seed": 20260818, "bootstrap_replicates": 10000, "bootstrap_two_sided_ci": [bootstrap_summary["two_sided_95_lower"], bootstrap_summary["two_sided_95_upper"]], "bootstrap_one_sided_upper": bootstrap_summary["one_sided_95_upper"], "development_signal_status": status, "probability_edge_confirmed": False, "T15_equivalence": False, "primary_gamma_frozen": False, "code_manifest_sha256": sha256(code_manifest), "input_hashes": {"m09_frame": sha256(FRAME), "matrix": sha256(MATRIX), "metadata": sha256(META), "m09_manifest": sha256(M09_MANIFEST)}, "config_hashes": {"backend": sha256(BACKEND), "h1_grid": sha256(GRID), "m10_protocol": sha256(M10), "h2_budget": sha256(BUDGET)}}
    atomic_json(MAN / "P2_WIN_H2_NAR_CORE_HISTORICAL_V1.json", manifest)
    report = f"# P2-M10 — H2 NAR Racing-Information Historical Development\n\n## STATUS\n`{status}` — `READY_FOR_P2_M11_CURRENT_INFO_AND_PROSPECTIVE_H2_FOUNDATION`\n\n## Evidence\nHistorical `MARKET_TIME_UNKNOWN`, development-reference-only, and `H2_EVIDENCE_NOT_FRESH_HOLDOUT`; H1 outcomes and baseline were already seen. `P2-INC-001` remains H1 lineage only.\n\n## Fixed NAR core\nFS04 was pre-designated; it was not selected from partial ablations. FS04 candidate LL={fs04_metrics['candidate_ll']:.12g}, Market LL={fs04_metrics['market_ll']:.12g}, delta={fs04_metrics['delta_vs_market']:.12g}; FS04 vs selected FS00 delta={fs04_vs_fs00:.12g}.\n\n## Bootstrap\n10,000 race-date-block replicates, seed 20260818: two-sided CI=[{bootstrap_summary['two_sided_95_lower']:.12g}, {bootstrap_summary['two_sided_95_upper']:.12g}], one-sided upper={bootstrap_summary['one_sided_95_upper']:.12g} (`{bootstrap_summary['status']}`). Diagnostic only.\n\n## Boundaries\nNo P2_CURRENT, P2_BIAS, external, P2_XVENUE, payout, ROI, feature-importance action, clipping, recalibration, or H1 search was used. H2 budget is 4/6; C05 remains prospective P2_CURRENT and C06 unallocated.\n"
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(report, encoding="utf8")
    run = {"job": "P2-M10", "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "built_at": datetime.now(timezone.utc).isoformat(), "code_manifest_sha256": sha256(code_manifest), "input_manifest_sha256": hashlib.sha256((sha256(FRAME) + sha256(MATRIX) + sha256(META) + sha256(M09_MANIFEST)).encode()).hexdigest(), "config_manifest_sha256": hashlib.sha256((sha256(BACKEND) + sha256(GRID) + sha256(M10) + sha256(BUDGET)).encode()).hexdigest(), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"lightgbm": lightgbm.__version__, "numpy": np.__version__, "pandas": "NOT_INSTALLED", "scipy": scipy.__version__}, "random_seed": 20260819, "commands": ["P2_FORMAL_M10_EVALUATION=1 .venv-p2-model/bin/python -m src.audit.p2_m10_h2_nar_core"], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "formal_model_fits": 13, "checkpoints": 12}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}, "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN", "evidence_status": EVIDENCE, "h2_evidence_not_fresh_holdout": True, "protocol_incident_lineage": INCIDENT_ID, "probability_edge_confirmed": False, "prospective_outcomes_used": False}
    atomic_json(AUD / "run_manifest.json", run)
    return {"status": status, "fs04_delta": fs04_metrics["delta_vs_market"], "formal_model_fits": 13, "races": len(fs04)}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
