"""Official card/detail annotation audit; no results or model data are opened."""
from __future__ import annotations

import csv
from pathlib import Path

from src.ingestion.adapters import nankan_official as official

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12b_r5" / "detail_annotation_audit.csv"
SAMPLES = (
    "https://www.nankankeiba.com/syousai/2026080219050101.do",
    "https://www.nankankeiba.com/syousai/2026080718050101.do",
)


def main() -> None:
    rows = []
    for url in SAMPLES:
        page = official.fetch_race_page(url, 15)
        html = official.decode_html(page.raw, page.headers.get("Content-Type"))
        identity = official.resolve_race(page.final_url, html)
        for card in official.parse_current_card_identity(html, identity=identity):
            detail_page = official.fetch_race_page(card["official_horse_url"], 15)
            detail = official.parse_official_horse_detail(official.decode_html(detail_page.raw, detail_page.headers.get("Content-Type")), official_horse_id=card["official_horse_id"])
            rows.append({"race_key": f"{identity['race_date']}_{identity['venue']}_{int(identity['race_number']):02d}", "horse_number": card["horse_number"], "card_horse_name": card["horse_name_exact"], "horse_detail_name_raw": detail["horse_detail_name_raw"], "horse_detail_name_identity": detail["horse_detail_name_identity"], "horse_registration_status": detail["horse_registration_status"] or "", "comparison_status": "EXACT" if card["horse_name_exact"] == detail["horse_detail_name_identity"] else "CONFLICT"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    conflicts = [row for row in rows if row["comparison_status"] != "EXACT"]
    if conflicts: raise RuntimeError(f"BLOCK_SOURCE_NAME_ANNOTATION_UNRESOLVED:{conflicts}")
    print({"detail_pages_inspected": len(rows), "exact_deregistered_suffix_count": sum(row["horse_registration_status"] == "DEREGISTERED" for row in rows), "remaining_conflicts": 0})


if __name__ == "__main__":
    main()
