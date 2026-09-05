"""Raw-preserving NAR corner-order tokenization without group/tie inference."""
from __future__ import annotations

import json
import re

_FW = str.maketrans("０１２３４５６７８９", "0123456789")
_NUM = re.compile(r"[0-9０-９]+")


def parse_corners(raw: str | None) -> dict:
    if raw in (None, ""):
        return {"corner_parse_status": "CORNER_MISSING", "corners": []}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {"corner_parse_status": "CORNER_JSON_PARSE_FAILURE", "corners": []}
    if not isinstance(decoded, list):
        return {"corner_parse_status": "CORNER_NOT_ARRAY", "corners": []}
    corners = []
    for index, entry in enumerate(decoded, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("order_raw"), str):
            return {"corner_parse_status": "CORNER_ENTRY_INVALID", "corners": []}
        order_raw = entry["order_raw"]
        groups = []
        # Comma delimits a raw group. A hyphen/parenthesis/equals remains inside
        # the raw group and is not interpreted as an equal placing or a tie.
        for group_id, token in enumerate(re.split(r"[,，]", order_raw), 1):
            numbers = [int(value.translate(_FW)) for value in _NUM.findall(token)]
            groups.append({"group_id": group_id, "raw_group_token": token, "horse_numbers": numbers, "group_size": len(numbers), "group_semantic": "GROUP_SEMANTIC_UNVERIFIED" if len(numbers) != 1 or any(symbol in token for symbol in "()-=") else "SINGLE_TOKEN"})
        corners.append({"corner_no": index, "corner_name": entry.get("name"), "order_raw": order_raw, "groups": groups})
    return {"corner_parse_status": "CORNER_TOKENIZED_RAW_ORDER", "corners": corners}


def completeness(corner: dict, active_horses: set[int]) -> dict:
    flattened = [horse for group in corner["groups"] for horse in group["horse_numbers"]]
    seen = set(flattened)
    duplicate = sorted(horse for horse in seen if flattened.count(horse) > 1)
    return {"expected_runners": len(active_horses), "parsed_unique_horses": len(seen), "missing_horses": sorted(active_horses - seen), "extra_horses": sorted(seen - active_horses), "duplicate_horses": duplicate, "complete": seen == active_horses and not duplicate, "has_ambiguous_group": any(group["group_semantic"] != "SINGLE_TOKEN" for group in corner["groups"])}
