from pathlib import Path

from src.features.legacy_v1.builder import reconstruct_v1_status
from src.features.legacy_v1.contracts import GROUP_BY_FEATURE, LEGACY_FEATURES


ROOT = Path(__file__).resolve().parents[2]


def test_v1_feature_count_exactly_119_and_f4_absent():
    assert len(LEGACY_FEATURES) == 119
    assert set(GROUP_BY_FEATURE.values()) == {"F0", "F1", "F2", "F3", "F5", "F6", "F7", "F8"}


def test_audited_status_reconstruction_is_explicit():
    assert reconstruct_v1_status("FINISHED", None) == "FINISHED"
    assert reconstruct_v1_status("RAW_FINISH_STATUS_MISSING", "出走取消") == "SCRATCHED"
    try:
        reconstruct_v1_status("RAW_FINISH_STATUS_MISSING", "unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown raw status must not be inferred")


def test_last_seen_and_market_sources_not_in_active_builder():
    text = (ROOT / "src/features/legacy_v1/builder.py").read_text(encoding="utf-8")
    assert "last_seen_date" not in text
    for prohibited in ("odds", "popularity", "payout", "keibabook"):
        assert prohibited not in text.lower()


def test_feature_set_registry_is_exactly_frozen():
    import json

    payload = json.loads((ROOT / "configs/features/P2_MAIN_FEATURE_SET_REGISTRY_V1.yaml").read_text(encoding="utf-8"))
    assert [x["feature_set_id"] for x in payload["sets"]] == [
        "FS00_LEGACY", "FS01_LEGACY_SPD", "FS02_LEGACY_SPD_PACE",
        "FS03_LEGACY_SPD_PACE_CLASS_RULE", "FS04_LEGACY_SPD_PACE_CLASS_FULL",
    ]


def test_integrated_matrix_is_label_free_and_namespaced():
    import csv
    import gzip

    path = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert len(reader.fieldnames) == 178
        assert all(name.startswith(("V1__", "P2_CLASS_", "P2_SPD__", "P2_PACE__")) for name in reader.fieldnames)
        assert not {"finish_position", "result_status", "payout", "target_win"} & set(reader.fieldnames)
