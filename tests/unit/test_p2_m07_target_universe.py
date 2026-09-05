from pathlib import Path

from src.audit.p2_m07_target_universe import classify_race, starter_status


ROOT = Path(__file__).resolve().parents[2]


def base(**updates):
    row = {
        "conditions_raw": "", "race_name": "", "race_type_raw": "", "jra_exchange_flag": "0",
        "newcomer_flag": "0", "local_exchange_flag": "0", "class_codes_json": "[]",
        "race_taxonomy_code": "AGE_CONDITIONED_UNGRADED", "race_grade_code": "NONE",
    }
    row.update(updates)
    return row


def test_primary_precedence_and_class_floor():
    assert classify_race(base(jra_exchange_flag="1"))[0] == "PRIMARY_EXCLUDED"
    assert classify_race(base(newcomer_flag="1"))[1] == "NEWCOMER"
    assert classify_race(base(class_codes_json='["C3"]'))[1] == "BELOW_PRIMARY_CLASS_FLOOR_C3"
    assert classify_race(base(class_codes_json='["C2"]'))[0] == "PRIMARY_ELIGIBLE"
    assert classify_race(base(class_codes_json='["B3","C2"]'))[0] == "PRIMARY_ELIGIBLE"


def test_special_and_exchange_semantics():
    assert classify_race(base(race_taxonomy_code="OPEN"))[0] == "PRIMARY_ELIGIBLE"
    assert classify_race(base(local_exchange_flag="1"))[1] == "LOCAL_EXCHANGE_CLASS_FLOOR_UNVERIFIABLE"
    assert classify_race(base(conditions_raw="地方交流"))[1] == "UNRESOLVED_EXCHANGE_TYPE"
    assert classify_race(base())[1] == "CLASS_FLOOR_UNVERIFIABLE"


def test_outcome_status_is_explicit_and_safe():
    assert starter_status("FINISHED", None, 1) == "STARTER_VALID_FINISH"
    assert starter_status("RAW_FINISH_STATUS_MISSING", "競走中止", None) == "STARTER_NO_VALID_FINISH"
    assert starter_status("RAW_FINISH_STATUS_MISSING", "出走取消", None) == "NONSTARTER"
    assert starter_status("RAW_FINISH_STATUS_MISSING", "unknown", None) == "UNRESOLVED_OUTCOME_STATUS"


def test_market_and_feature_matrix_are_not_inputs():
    text = (ROOT / "src/audit/p2_m07_target_universe.py").read_text(encoding="utf-8").lower()
    assert "market_snapshot.sqlite" not in text
    assert "nankan_market.sqlite" not in text
    assert "keibabook_files_opened\": 0" in text
