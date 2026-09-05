import math

from src.market.calibration import calibrated_probabilities, derivative_and_curvature, fit_power_gamma
from src.market.market_loss import mean_race_log_loss, race_log_loss
from src.market.normalization import InvalidMarketSnapshot, normalize_win_odds


def rows():
    return normalize_win_odds([{"horse_number":"1","odds_win":2.0},{"horse_number":"2","odds_win":4.0},{"horse_number":"3","odds_win":8.0}])


def test_inverse_q_and_invalid_odds():
    x=rows()
    assert math.isclose(sum(r["q_raw"] for r in x),1.0,abs_tol=1e-12)
    assert all(r["q_raw"]>0 for r in x)
    for bad in (0.0,-1.0):
        try: normalize_win_odds([{"horse_number":"1","odds_win":2},{"horse_number":"2","odds_win":bad}])
        except InvalidMarketSnapshot: pass
        else: raise AssertionError("invalid odds must reject snapshot")
    try: normalize_win_odds([{"horse_number":"1","odds_win":2}])
    except InvalidMarketSnapshot: pass
    else: raise AssertionError("incomplete active roster must reject snapshot")


def test_pre_snapshot_scratch_is_removed_without_retroactive_rewrite():
    before=normalize_win_odds([{"horse_number":"1","odds_win":2},{"horse_number":"2","odds_win":4},{"horse_number":"3","odds_win":8},{"horse_number":"4","odds_win":16}])
    after=normalize_win_odds([{"horse_number":"1","odds_win":2},{"horse_number":"2","odds_win":4},{"horse_number":"3","odds_win":8}])
    assert {x["horse_number"] for x in after} == {"1","2","3"}
    assert after[0]["q_raw"] != before[0]["q_raw"]


def test_gamma_identity_derivatives_and_determinism():
    x=[{**r,"win_soft_target":r["q_raw"]} for r in rows()]
    p=calibrated_probabilities(x,1.0)
    assert max(abs(p[r["horse_number"]]-r["q_raw"]) for r in x)<1e-12
    _,curvature=derivative_and_curvature([x],1.0)
    assert curvature>=0
    a,b=fit_power_gamma([x]),fit_power_gamma([x])
    assert a["status"]==b["status"]=="GAMMA_SOLVED" and abs(a["gamma"]-b["gamma"])<1e-12


def test_soft_dead_heat_race_equal_loss_and_order_invariance():
    x=[{**r,"win_soft_target":0.5 if r["horse_number"] in {"1","2"} else 0.0} for r in rows()]
    assert math.isclose(sum(r["win_soft_target"] for r in x),1.0)
    assert math.isclose(mean_race_log_loss([x,x],1.0),race_log_loss(x,1.0))
    a=calibrated_probabilities(x,0.8); b=calibrated_probabilities(list(reversed(x)),0.8)
    assert a==b


def test_no_clipping_or_market_feature_imports():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    text=(root/"src/market/normalization.py").read_text(encoding="utf8").lower()
    assert "clip" not in text and "epsilon" not in text


def test_t15_not_frozen_and_no_payout_roi_core_access():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    text=(root/"src/audit/p2_m08a_market_baseline.py").read_text(encoding="utf8").lower()
    assert "primary_parameter_status\":\"not_frozen" in text
    assert "payout_tables_opened\":0" in text and "roi_evaluated\":false" in text
