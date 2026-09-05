from pathlib import Path

from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]


def _raw_for(date: str, venue: str, number: int) -> Path:
    key = f"P2_RACE_V1::{date}\x1f{venue}\x1f{number}"
    root = ROOT / "data" / "raw" / "live_development_results"
    candidates = sorted((root / key).glob("result_*.html"))
    if not candidates:
        candidates = sorted((root / f"{date}_{venue}_{number:02d}").glob("result_*.html"))
    return candidates[-1]


def test_retained_official_normal_win_and_wide_payouts_are_parsed() -> None:
    raw = _raw_for("2026-08-24", "船橋", 5).read_bytes()
    parsed = official.parse_official_result(
        official.decode_html(raw),
        identity={"race_date": "2026-08-24", "venue": "船橋", "race_number": 5},
    )
    payouts = {(item["ticket_type"], item["combination_raw"]): item["payout_amount"] for item in parsed["payouts"]}
    assert payouts[("WIN", "7")] == 250
    assert payouts[("WIDE", "1-8")] == 2750
    assert sum(item["ticket_type"] == "WIDE" for item in parsed["payouts"]) == 3
    assert official.parse_official_refund_horse_numbers(official.decode_html(raw))["status"] == "NO_REFUND"


def test_retained_official_exact_refund_annotation_is_not_guessed() -> None:
    raw = _raw_for("2026-08-20", "川崎", 11).read_bytes()
    assert official.parse_official_refund_horse_numbers(official.decode_html(raw)) == {
        "status": "REFUND_HORSE_NUMBERS", "horse_numbers": [3, 9], "raw_notes": ["返還：3,9号馬"],
    }


def test_unknown_refund_annotation_requires_review() -> None:
    html = "<div class='pc'><table><tr><th>備考</th></tr><tr><td>返還対象あり</td></tr></table></div>"
    assert official.parse_official_refund_horse_numbers(html)["status"] == "REFUND_REVIEW_REQUIRED"
