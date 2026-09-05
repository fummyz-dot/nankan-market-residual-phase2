"""Nankan official adapter based on retained historical fixture DOMs.

This module has no live-freshness claim. Callers must label historical pages as
`HISTORICAL_FIXTURE_ONLY` and must not promote parsed values to a live snapshot.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from src.validation.current_info_sanitizer import sanitize_current_info

OFFICIAL_HOSTS = {"www.nankankeiba.com", "nankankeiba.com"}
# URL venue codes are optional cross-checks only.  The race page's displayed
# venue remains the authoritative identity source.  The original fixture only
# covered Kawasaki, so an unrecognised URL code must not manufacture a venue or
# reject another official South-Kanto page solely for lacking a fixture map.
OBSERVED_VENUE_CODE = {"21": "川崎"}
# A normal conditions race can use this exact class-only title instead of a
# distinct race name. Keep the text as a raw class/conditions label; do not
# infer a name from it. This intentionally conservative pattern covers the
# observed `Ｃ２(三)(四)` form only.
_CONDITIONS_ONLY_TITLE = re.compile(r"^[ＣC][０-９0-9]+(?:\([一二三四五六七八九十]+\))*$")
_ROOT = Path(__file__).resolve().parents[3]
_RESULT_STATUS_VOCABULARY = _ROOT / "configs" / "evaluation" / "P2_OFFICIAL_RESULT_STATUS_VOCABULARY_V1.yaml"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FetchResult:
    requested_url: str
    request_started_at: str
    captured_at: str
    final_url: str
    redirect_chain: list[dict[str, Any]]
    status_code: int
    headers: dict[str, str]
    raw: bytes


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Any] = field(default_factory=list)
    parent: "Node | None" = None


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {k.lower(): v or "" for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in {"br", "img", "meta", "link", "input", "hr", "source", "area", "base", "embed", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def decode_html(raw: bytes, content_type: str | None = None) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    candidates = [charset_match.group(1)] if charset_match else []
    candidates.extend(["cp932", "shift_jis", "utf-8"])
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_html(html: str) -> Node:
    parser = _Tree(); parser.feed(html); parser.close()
    return parser.root


def iter_nodes(node: Node, tag: str | None = None):
    for child in node.children:
        if isinstance(child, Node):
            if tag is None or child.tag == tag:
                yield child
            yield from iter_nodes(child, tag)


def node_text(node: Node) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, Node):
            parts.append(node_text(child))
        else:
            parts.append(child)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def node_text_raw(node: Node) -> str:
    """Return a displayed node value without changing internal characters.

    This is deliberately narrower than :func:`node_text`.  Official person
    category labels can contain a significant full-width space (for example
    ``所　蛍``); converting it to an ASCII space would no longer be the frozen
    M01/V1 text token.  Only leading/trailing document whitespace is removed.
    """
    parts: list[str] = []
    for child in node.children:
        parts.append(node_text_raw(child) if isinstance(child, Node) else child)
    return "".join(parts).strip()


def direct_cells(row: Node) -> list[Node]:
    return [child for child in row.children if isinstance(child, Node) and child.tag in {"th", "td"}]


def following_table(root: Node, title: str) -> Node:
    seen_title = False
    for node in iter_nodes(root):
        if node.tag in {"p", "h4", "h3"} and node_text(node) == title:
            seen_title = True
        elif seen_title and node.tag == "table":
            return node
    raise ValueError(f"table following title not found: {title}")


def url_identity(url: str) -> dict[str, Any]:
    match = re.search(r"/(?:syousai|uma_shosai|odds|result)/(\d{16})\.do", urllib.parse.urlparse(url).path)
    if not match:
        raise ValueError("official race URL must include a 16-digit race identifier")
    value = match.group(1)
    date = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return {"race_date": date, "venue": OBSERVED_VENUE_CODE.get(value[8:10]), "race_number": int(value[-2:]), "race_id_raw": value}


def parse_race_identity(html: str) -> dict[str, Any]:
    root = parse_html(html)
    subtitle = " ".join(node_text(n) for n in iter_nodes(root, "p") if "10R" in node_text(n) or "発走時刻" in node_text(n))
    text = node_text(root)
    date = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    race = re.search(r"(?:^|\s)(\d{1,2})R(?:\s|$)", subtitle or text)
    venue = re.search(r"(大井|船橋|川崎|浦和)競馬", text)
    post = re.search(r"発走時刻\s*(\d{1,2}:\d{2})", text)
    # Official pages can express the surface as `ダ900m` or `ダート1400m`.
    # Accept only a displayed numeric distance; do not manufacture one.
    distance = re.search(r"(ダ(?:ート)?|芝)\s*(\d{1,2}(?:,\d{3})|\d{3,4})m", text)
    field_size = re.search(r"[（(]\s*(\d{1,2})頭\s*[）)]", text)
    title_nodes = [n for n in iter_nodes(root, "span") if "nk23_c-tab1__title__text" in n.attrs.get("class", "")]
    title_text = node_text(title_nodes[0]) if title_nodes else None
    race_name = None if title_text and _CONDITIONS_ONLY_TITLE.fullmatch(title_text) else title_text
    conditions_raw = title_text if title_text and _CONDITIONS_ONLY_TITLE.fullmatch(title_text) else None
    required = {
        "race_date": date,
        "race_number": race,
        "venue": venue,
        "scheduled_post_time": post,
        "distance_m": distance,
        "field_size": field_size,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(f"required race identity field missing from page body: {', '.join(missing)}")
    return {"race_date": f"{date.group(1)}-{int(date.group(2)):02d}-{int(date.group(3)):02d}", "venue": venue.group(1), "race_number": int(race.group(1)), "race_name": race_name, "conditions_raw": conditions_raw, "scheduled_post_time_local": post.group(1), "distance_m": int(distance.group(2).replace(",", "")), "surface": "ダート" if distance.group(1).startswith("ダ") else "芝", "field_size": int(field_size.group(1))}


def resolve_race(url: str, html: str) -> dict[str, Any]:
    from_url = url_identity(url)
    from_page = parse_race_identity(html)
    keys = ("race_date", "race_number")
    if from_url["venue"] is not None:
        keys = (*keys, "venue")
    if any(from_url[key] != from_page[key] for key in keys):
        raise ValueError(f"URL/page race identity mismatch: url={from_url}, page={from_page}")
    return from_page


_PRE_RACE_WITHDRAWN_TOKEN = "取消"
_PRE_RACE_ACTIVE = "ACTIVE"
_PRE_RACE_WITHDRAWN = "PRE_RACE_WITHDRAWN"


def _current_card_identity_table(root: Node) -> Node:
    """Return the official detailed-card identity table used by current parsers."""
    target = next((table for table in iter_nodes(root, "table")
                   if "馬番" in [node_text(cell) for cell in iter_nodes(table, "th")]
                   and any("馬名" in node_text(cell) and "生年月日" in node_text(cell) for cell in iter_nodes(table, "th"))
                   and any(re.fullmatch(r"/uma_info/\d+\.do", node.attrs.get("href", "")) for node in iter_nodes(table, "a"))), None)
    if target is None:
        raise ValueError("current card identity table not found")
    return target


def _direct_table_rows(table: Node):
    """Yield only rows belonging to this exact table, never nested table rows."""
    for row in iter_nodes(table, "tr"):
        ancestor = row.parent
        while ancestor is not None and ancestor.tag != "table":
            ancestor = ancestor.parent
        if ancestor is table:
            yield row


def _current_card_row_number(cells: list[Node]) -> tuple[int, int] | None:
    values = [node_text(cell) for cell in cells]
    leading = [index for index, value in enumerate(values[:2]) if re.fullmatch(r"\d+", value)]
    if not leading:
        return None
    index = leading[-1]
    return int(values[index]), index


def _current_card_row_status(cells: list[Node], *, horse_number: int, horse_index: int) -> tuple[str | None, str]:
    """Classify the sole approved pre-race roster status before active parsing.

    Only a dedicated exact cell between the displayed horse number and the
    official horse-name/detail cell is a runner-status channel.  This avoids
    interpreting result status, arbitrary annotations, or text elsewhere on
    the page as a withdrawal signal.
    """
    horse_cells = [
        index for index, cell in enumerate(cells)
        if any(re.fullmatch(r"/uma_info/\d+\.do", node.attrs.get("href", "")) for node in iter_nodes(cell, "a"))
    ]
    if len(horse_cells) != 1:
        raise ValueError(f"OFFICIAL_PRE_RACE_CARD_HORSE_CELL_UNRESOLVED:{horse_number}")
    horse_cell_index = horse_cells[0]
    if horse_cell_index <= horse_index:
        raise ValueError(f"OFFICIAL_PRE_RACE_CARD_HORSE_CELL_ORDER_UNRESOLVED:{horse_number}")
    status_values = [node_text(cell) for cell in cells[horse_index + 1:horse_cell_index] if node_text(cell)]
    if not status_values:
        return None, _PRE_RACE_ACTIVE
    if status_values == [_PRE_RACE_WITHDRAWN_TOKEN]:
        return _PRE_RACE_WITHDRAWN_TOKEN, _PRE_RACE_WITHDRAWN
    raw = "|".join(status_values)
    raise ValueError(f"BLOCK_PRE_RACE_RUNNER_STATUS_UNRESOLVED:{horse_number}:{raw}")


def parse_pre_race_card_runner_statuses(html: str, *, identity: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Preserve pre-race roster status before the normal active-runner parser.

    The exact official token ``取消`` is a target-roster event only.  It is not
    a result status and never changes historical outcome semantics.  The raw
    card row remains represented for audit while only ``ACTIVE`` rows may
    proceed to target current/feature materialization.
    """
    target = _current_card_identity_table(parse_html(html))
    output: dict[int, dict[str, Any]] = {}
    for row in _direct_table_rows(target):
        cells = [cell for cell in direct_cells(row) if cell.tag == "td"]
        number = _current_card_row_number(cells)
        if number is None:
            continue
        horse_number, horse_index = number
        if horse_number in output:
            raise ValueError(f"OFFICIAL_PRE_RACE_CARD_DUPLICATE_HORSE_NUMBER:{horse_number}")
        raw_status, normalized_status = _current_card_row_status(
            cells, horse_number=horse_number, horse_index=horse_index
        )
        horse_cell = next(
            cell for cell in cells
            if any(re.fullmatch(r"/uma_info/\d+\.do", node.attrs.get("href", "")) for node in iter_nodes(cell, "a"))
        )
        anchors = [node for node in iter_nodes(horse_cell, "a") if re.fullmatch(r"/uma_info/(\d+)\.do", node.attrs.get("href", ""))]
        if len(anchors) != 1:
            raise ValueError(f"OFFICIAL_PRE_RACE_CARD_HORSE_ANCHOR_UNRESOLVED:{horse_number}")
        match = re.fullmatch(r"/uma_info/(\d+)\.do", anchors[0].attrs["href"])
        assert match is not None
        output[horse_number] = {
            "horse_number": horse_number,
            "horse_name_raw": node_text(anchors[0]).strip(),
            "runner_status_raw": raw_status,
            "normalized_status": normalized_status,
            "official_horse_id": match.group(1),
            "official_horse_url": urllib.parse.urljoin("https://www.nankankeiba.com", anchors[0].attrs["href"]),
            # The roster parser never performs identity resolution.  A caller
            # may retain its approved resolver outcome separately without
            # making target feature generation depend on a withdrawn row.
            "identity_resolution_status": "NOT_ATTEMPTED",
        }
    active = [row for row in output.values() if row["normalized_status"] == _PRE_RACE_ACTIVE]
    if len(active) != int(identity["field_size"]):
        raise ValueError(
            f"OFFICIAL_PRE_RACE_ACTIVE_RUNNER_COUNT_MISMATCH:{len(active)}:{identity['field_size']}"
        )
    return output


def parse_bodyweight(html: str, *, identity: dict[str, Any], captured_at: str) -> dict[str, Any]:
    root = parse_html(html)
    target = None
    for table in iter_nodes(root, "table"):
        headings = "|".join(node_text(cell) for cell in iter_nodes(table, "th"))
        has_runner_row = any(
            len([cell for cell in direct_cells(row) if cell.tag == "td"]) >= 6
            and re.fullmatch(r"\d+", [node_text(cell) for cell in direct_cells(row) if cell.tag == "td"][0]) is not None
            for row in iter_nodes(table, "tr")
        )
        if "馬体重" in headings and "馬番" in headings and has_runner_row:
            target = table; break
    if target is None:
        raise ValueError("bodyweight table not found")
    statuses = parse_pre_race_card_runner_statuses(html, identity=identity)
    active_numbers = {number for number, row in statuses.items() if row["normalized_status"] == _PRE_RACE_ACTIVE}
    runners = []
    for row in iter_nodes(target, "tr"):
        cells = direct_cells(row)
        td_values = [node_text(cell) for cell in cells if cell.tag == "td"]
        leading_numbers = [int(value) for value in td_values[:2] if re.fullmatch(r"\d+", value)]
        if len(td_values) < 6 or not leading_numbers:
            continue
        horse_number = leading_numbers[-1]
        if horse_number not in active_numbers:
            continue
        weight_text = next((value for value in td_values if re.search(r"\d{3}\s*[+＋\-－±]\s*\d+", value)), "")
        match = re.search(r"(\d{3})\s*([+＋\-－±])\s*(\d+)", weight_text)
        if match:
            sign = {"+": 1, "＋": 1, "-": -1, "－": -1, "±": 0}[match.group(2)]
            runners.append({"horse_number": horse_number, "body_weight": int(match.group(1)), "body_weight_change": sign * int(match.group(3))})
            continue
        # The retained 2026-09-01 Ohi current cards use this exact ASCII
        # placeholder when an absolute weight is published but its change is not.
        missing_change = next((match for value in td_values if (match := re.fullmatch(r"\s*(\d{3})\s*-\s*", value)) is not None), None)
        if missing_change is None:
            continue
        runners.append({"horse_number": horse_number, "body_weight": int(missing_change.group(1)), "body_weight_change": None})
    if len(runners) != len(active_numbers):
        raise ValueError(f"bodyweight runner count mismatch: {len(runners)} != {len(active_numbers)}")
    return sanitize_current_info({**{key: identity[key] for key in ("race_date", "venue", "race_number")}, "captured_at": captured_at, "runners": runners})


_CURRENT_CARD_JOCKEY_LINK = re.compile(r"/kis_info/(\d+)\.do")


def _parse_current_card_declared_jockeys(
    html: str,
    *,
    active_numbers: set[int],
) -> tuple[dict[int, str | None], list[dict[str, Any]]]:
    """Read declared jockey text only from its explicit official person link.

    The entry table may omit a frame-number cell for some rows.  Therefore a
    fixed header-to-cell index is not a valid source binding: on the retained
    Funabashi cards it can select the adjacent sire/dam cell.  The official
    ``/kis_info/<id>.do`` anchor inside the same direct row is the sole
    approved jockey source.  Its direct predecessor must be the official
    assigned-weight cell, which prevents an unrelated ``kis_info`` link in a
    neighbouring pedigree/person field from becoming a jockey.  If that
    source is absent or ambiguous, retain a null rather than borrowing text
    from pedigree or another neighbouring field.
    """
    identities, warnings = parse_current_card_declared_jockey_identities(
        html, active_numbers=active_numbers
    )
    return {
        horse_number: item["declared_jockey_raw"]
        for horse_number, item in identities.items()
    }, warnings


def _parse_current_card_declared_jockeys_from_table(
    target: Node,
    *,
    active_numbers: set[int],
    rows: list[Node] | None = None,
) -> tuple[dict[int, str | None], list[dict[str, Any]]]:
    """Compatibility view of the explicit jockey identity parser.

    Core P2_CURRENT storage intentionally retains only the declared display
    name.  Research provenance also needs the exact official ``kis_info`` ID,
    so the source binding lives in the identity helper below.  Keeping this
    wrapper preserves the established parser contract for existing callers.
    """
    identities, warnings = _parse_current_card_declared_jockey_identities_from_table(
        target, active_numbers=active_numbers, rows=rows
    )
    return {
        horse_number: item["declared_jockey_raw"]
        for horse_number, item in identities.items()
    }, warnings


def parse_current_card_declared_jockey_identities(
    html: str,
    *,
    active_numbers: set[int],
) -> tuple[dict[int, dict[str, str | None]], list[dict[str, Any]]]:
    """Return official declared jockey IDs from the same direct card cell.

    This is additive provenance for prospective research.  It deliberately
    shares the corrected P2_CURRENT binding: exactly one ``/kis_info/<id>.do``
    anchor in the runner's own direct table row.  No neighbouring pedigree
    text, name dictionary, or inferred identity is ever consulted.
    """
    target = _current_card_identity_table(parse_html(html))
    return _parse_current_card_declared_jockey_identities_from_table(
        target, active_numbers=active_numbers
    )


def _parse_current_card_declared_jockey_identities_from_table(
    target: Node,
    *,
    active_numbers: set[int],
    rows: list[Node] | None = None,
) -> tuple[dict[int, dict[str, str | None]], list[dict[str, Any]]]:
    """Implement explicit jockey extraction for the given official entry rows.

    ``rows`` exists only to let the parser's order-independence be regression
    tested.  Production always uses this exact table's direct rows.
    """
    jockey_by_number: dict[int, dict[str, str | None]] = {}
    warnings: list[dict[str, Any]] = []
    seen: set[int] = set()

    for row in _direct_table_rows(target) if rows is None else rows:
        cells = [cell for cell in direct_cells(row) if cell.tag == "td"]
        number = _current_card_row_number(cells)
        if number is None:
            continue
        horse_number, _ = number
        if horse_number not in active_numbers:
            continue
        if horse_number in seen:
            raise ValueError(f"OFFICIAL_CURRENT_JOCKEY_DUPLICATE_RUNNER_ROW:{horse_number}")
        seen.add(horse_number)

        jockey_cells = [
            cell for index, cell in enumerate(cells)
            if index > 0
            and re.fullmatch(r"[▲△◇☆]?\d+(?:\.\d+)?", node_text(cells[index - 1])) is not None
            and any(_CURRENT_CARD_JOCKEY_LINK.fullmatch(anchor.attrs.get("href", "")) for anchor in iter_nodes(cell, "a"))
        ]
        anchors = [
            anchor for cell in jockey_cells for anchor in iter_nodes(cell, "a")
            if _CURRENT_CARD_JOCKEY_LINK.fullmatch(anchor.attrs.get("href", ""))
        ]
        if len(jockey_cells) == 1 and len(anchors) == 1:
            display = node_text(jockey_cells[0]).strip()
            if display:
                match = _CURRENT_CARD_JOCKEY_LINK.fullmatch(anchors[0].attrs.get("href", ""))
                if match is None:  # Defensive: the same predicate selected it.
                    raise ValueError("OFFICIAL_CURRENT_JOCKEY_LINK_PARSE_FAILED")
                jockey_by_number[horse_number] = {
                    "declared_jockey_id": match.group(1),
                    "declared_jockey_raw": display,
                    "jockey_source_status": "RESOLVED_OFFICIAL",
                }
                continue
            reason = "EXPLICIT_JOCKEY_DISPLAY_EMPTY"
        elif not jockey_cells:
            reason = "EXPLICIT_JOCKEY_LINK_MISSING"
        else:
            reason = "EXPLICIT_JOCKEY_LINK_AMBIGUOUS"
        jockey_by_number[horse_number] = {
            "declared_jockey_id": None,
            "declared_jockey_raw": None,
            "jockey_source_status": "UNRESOLVED",
        }
        warnings.append({"code": "CURRENT_JOCKEY_UNRESOLVED", "horse_number": horse_number, "reason": reason})

    for horse_number in sorted(active_numbers - seen):
        jockey_by_number[horse_number] = {
            "declared_jockey_id": None,
            "declared_jockey_raw": None,
            "jockey_source_status": "UNRESOLVED",
        }
        warnings.append({"code": "CURRENT_JOCKEY_UNRESOLVED", "horse_number": horse_number, "reason": "CURRENT_CARD_RUNNER_ROW_MISSING"})
    return jockey_by_number, warnings


def parse_current_card(html: str, *, identity: dict[str, Any], captured_at: str) -> dict[str, Any]:
    """Parse only allow-listed P2_CURRENT card fields from a live entry card.

    The parser deliberately leaves weather/track condition unresolved unless a
    separately audited official source parser is added.  Odds columns can occur
    in the same table but are never read into curated output.
    """
    statuses = parse_pre_race_card_runner_statuses(html, identity=identity)
    active_numbers = {number for number, row in statuses.items() if row["normalized_status"] == _PRE_RACE_ACTIVE}
    body = parse_bodyweight(html, identity=identity, captured_at=captured_at)
    jockey_by_number, jockey_warnings = _parse_current_card_declared_jockeys(
        html, active_numbers=active_numbers
    )
    identity_rows = {row["horse_number"]: row for row in parse_current_card_identity(html, identity=identity)}
    runners = []
    for runner in body["runners"]:
        number = runner["horse_number"]
        if number not in identity_rows:
            raise ValueError(f"current-card identity missing for horse number: {number}")
        runners.append({**runner, "declared_jockey_raw": jockey_by_number.get(number), **identity_rows[number]})
    parsed = sanitize_current_info({**{key: body[key] for key in ("race_date", "venue", "race_number", "captured_at")}, "runners": runners})
    # Parser diagnostics are additive provenance, not P2_CURRENT model
    # fields.  They are generated only by the explicit-source parser above.
    parsed["warnings"] = jockey_warnings
    return parsed


def parse_current_card_identity(html: str, *, identity: dict[str, Any], allow_nonstarter_rows: bool = False) -> list[dict[str, Any]]:
    """Extract only official pre-race horse identity provenance from the card.

    The card's abbreviated date is retained as raw text. It is deliberately not
    expanded into a birth date here; callers must validate against the linked
    official horse-detail page.
    """
    root = parse_html(html)
    target = _current_card_identity_table(root)
    statuses = parse_pre_race_card_runner_statuses(html, identity=identity)
    output, seen = [], set()
    for row in _direct_table_rows(target):
        cells = [cell for cell in direct_cells(row) if cell.tag == "td"]
        number = _current_card_row_number(cells)
        if number is None:
            continue
        horse_number, horse_index = number
        if horse_number in seen:
            raise ValueError("duplicate current-card horse number for identity")
        if statuses[horse_number]["normalized_status"] == _PRE_RACE_WITHDRAWN:
            # Status was inspected before this active identity layout assumes
            # the next cell is the horse cell.
            continue
        horse_cell = [cell for cell in cells if cell.tag == "td"][horse_index + 1]
        anchors = [node for node in iter_nodes(horse_cell, "a") if re.fullmatch(r"/uma_info/(\d+)\.do", node.attrs.get("href", ""))]
        spans = [match.group(1) for node in iter_nodes(horse_cell, "span")
                 if (match := re.match(r"\s*(\d{2}\.\d{1,2}\.\d{1,2})(?:\([^)]*\))?\s*$", node_text(node)))]
        if len(anchors) != 1 or len(spans) != 1:
            raise ValueError(f"current-card identity fields unavailable for horse {horse_number}")
        match = re.fullmatch(r"/uma_info/(\d+)\.do", anchors[0].attrs["href"])
        raw_name = node_text(anchors[0]).strip()
        card_affiliation_prefix, name = split_official_card_affiliation_prefix(raw_name)
        if not raw_name or not name or match is None:
            raise ValueError(f"current-card identity malformed for horse {horse_number}")
        seen.add(horse_number)
        output.append({"horse_number": horse_number, "card_horse_name_raw": raw_name,
                       "card_affiliation_prefix": card_affiliation_prefix, "card_horse_name_identity": name,
                       "horse_name_exact": name, "birth_date_raw": spans[0], "official_horse_id": match.group(1),
                       "official_horse_url": urllib.parse.urljoin("https://www.nankankeiba.com", anchors[0].attrs["href"])})
    active_count = sum(row["normalized_status"] == _PRE_RACE_ACTIVE for row in statuses.values())
    if (len(output) != active_count if not allow_nonstarter_rows else len(output) < active_count):
        raise ValueError(f"current-card identity runner count mismatch: {len(output)} != {active_count}")
    return output


def _class_has(node: Node, token: str) -> bool:
    return token in node.attrs.get("class", "").split()


AFFILIATION_PREFIX_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "features" / "P2_OFFICIAL_RUNNER_AFFILIATION_PREFIX_V1.yaml"
LEADING_AFFILIATION_PREFIX = re.compile(r"^(\[[^\[\]]+\])")


def approved_affiliation_prefixes() -> dict[str, tuple[str, ...]]:
    """Read the deliberately small, explicit official-card prefix allowlist."""
    text = AFFILIATION_PREFIX_CONFIG.read_text(encoding="utf-8")
    output: dict[str, tuple[str, ...]] = {}
    token: str | None = None
    for line in text.splitlines():
        match = re.fullmatch(r'  "(\[[^\[\]]+\])":', line)
        if match:
            token = match.group(1)
            continue
        values = re.fullmatch(r"    official_affiliation_values: \[([^\]]*)\]", line)
        if values and token:
            output[token] = tuple(value.strip() for value in values.group(1).split(",") if value.strip())
            token = None
    if not output:
        raise ValueError("OFFICIAL_AFFILIATION_PREFIX_CONFIG_EMPTY")
    return output


def split_official_card_affiliation_prefix(raw_name: str, *, enforce_allowlist: bool = True) -> tuple[str | None, str]:
    """Separate only an exact approved leading official affiliation token."""
    match = LEADING_AFFILIATION_PREFIX.match(raw_name)
    if not match:
        if raw_name.startswith("["):
            raise ValueError("BLOCK_SOURCE_AFFILIATION_PREFIX_UNRESOLVED:MALFORMED")
        return None, raw_name
    prefix = match.group(1)
    identity_name = raw_name[len(prefix):]
    if not identity_name:
        raise ValueError("BLOCK_SOURCE_AFFILIATION_PREFIX_UNRESOLVED:EMPTY_NAME")
    if enforce_allowlist and prefix not in approved_affiliation_prefixes():
        raise ValueError(f"BLOCK_SOURCE_AFFILIATION_PREFIX_UNRESOLVED:{prefix}")
    return prefix, identity_name


def parse_official_card_affiliation_context(html: str) -> dict[int, dict[str, str | None]]:
    """Read official card trainer affiliation for prefix semantic auditing only."""
    root = parse_html(html)
    target = next((table for table in iter_nodes(root, "table")
                   if "馬番" in [node_text(cell) for cell in iter_nodes(table, "th")]
                   and any("調教師" in node_text(cell) for cell in iter_nodes(table, "th"))
                   and any(re.fullmatch(r"/uma_info/\d+\.do", node.attrs.get("href", "")) for node in iter_nodes(table, "a"))), None)
    if target is None:
        raise ValueError("OFFICIAL_CARD_AFFILIATION_CONTEXT_TABLE_UNRESOLVED")
    output: dict[int, dict[str, str | None]] = {}
    for row in iter_nodes(target, "tr"):
        cells = [cell for cell in direct_cells(row) if cell.tag == "td"]
        values = [node_text(cell) for cell in cells]
        leading = [index for index, value in enumerate(values[:2]) if re.fullmatch(r"\d+", value)]
        if not leading:
            continue
        horse_index = leading[-1]
        if horse_index + 1 >= len(cells):
            continue
        horse_anchors = [node for node in iter_nodes(cells[horse_index + 1], "a") if re.fullmatch(r"/uma_info/\d+\.do", node.attrs.get("href", ""))]
        trainer_cells = [cell for cell in cells if any(re.fullmatch(r"/cho_info/\d+\.do", node.attrs.get("href", "")) for node in iter_nodes(cell, "a"))]
        if len(horse_anchors) != 1 or len(trainer_cells) != 1:
            continue
        number = int(values[horse_index])
        horse_anchor = horse_anchors[0]
        raw_name = node_text(horse_anchor).strip()
        trainer_text = node_text(trainer_cells[0]).strip()
        affiliation = re.search(r"\(([^()]*)\)\s*$", trainer_text)
        item = {"horse_name_raw": raw_name, "trainer_affiliation": affiliation.group(1) if affiliation else None}
        if number in output and output[number] != item:
            raise ValueError(f"OFFICIAL_CARD_AFFILIATION_CONTEXT_CONFLICT:{number}")
        output[number] = item
    return output


_PERSON_LINK_PATTERNS = {
    "jockey": re.compile(r"/kis_info/(\d+)\.do"),
    "trainer": re.compile(r"/cho_info/(\d+)\.do"),
}


def _card_person_rows(table: Node) -> dict[int, dict[str, dict[str, str]]]:
    """Read exact person-link text by horse number from one official table."""
    output: dict[int, dict[str, dict[str, str]]] = {}
    for row in iter_nodes(table, "tr"):
        # A responsive official card can nest another table inside a wrapper.
        # Do not mix rows from nested detail/history tables into this table's
        # current roster.
        ancestor = row.parent
        while ancestor is not None and ancestor.tag != "table":
            ancestor = ancestor.parent
        if ancestor is not table:
            continue
        cells = [cell for cell in direct_cells(row) if cell.tag == "td"]
        values = [node_text(cell) for cell in cells]
        leading = [index for index, value in enumerate(values[:2]) if re.fullmatch(r"\d+", value)]
        nonstarter_statuses = {"除外", "取消"}
        if not leading and (not values or values[0] not in nonstarter_statuses):
            continue
        # An official ``除外`` card row uses the frame number in the first
        # position and replaces its horse-number cell with the status.  The
        # retained card still carries the exact roster number in its
        # ``writeOdds(<horse_number>)`` selector.  This is a roster binding,
        # not an odds value, and is used only for the already-approved R6
        # nonstarter row reconciliation.
        if (not leading and values[0] in nonstarter_statuses) or (len(leading) == 1 and len(values) > 1 and values[1] in nonstarter_statuses):
            selectors = {
                int(match.group(1)) for value in values
                for match in [re.fullmatch(r"writeOdds\((\d+)\);", value)]
                if match is not None
            }
            if len(selectors) != 1:
                raise ValueError("OFFICIAL_CARD_PERSON_NONSTARTER_HORSE_NUMBER_UNRESOLVED")
            horse_number = selectors.pop()
        else:
            horse_number = int(values[leading[-1]])
        people: dict[str, dict[str, str]] = {}
        for person_type, pattern in _PERSON_LINK_PATTERNS.items():
            anchors = [
                anchor for cell in cells for anchor in iter_nodes(cell, "a")
                if pattern.fullmatch(anchor.attrs.get("href", ""))
            ]
            if len(anchors) != 1:
                continue
            match = pattern.fullmatch(anchors[0].attrs["href"])
            assert match is not None
            people[person_type] = {
                "official_person_id": match.group(1),
                "display": node_text_raw(anchors[0]),
            }
        if len(people) != len(_PERSON_LINK_PATTERNS):
            continue
        if horse_number in output and output[horse_number] != people:
            raise ValueError(f"OFFICIAL_CARD_PERSON_CONTEXT_CONFLICT:{horse_number}")
        output[horse_number] = people
    return output


def _card_person_tokens(table: Node) -> dict[str, dict[str, str]]:
    """Read one exact compact-card display per official person ID.

    This deliberately does not use a row number.  An official nonstarter row
    can replace its displayed number with ``除外`` in the compact layout while
    retaining its person anchors; the official person ID remains the safe
    join key.
    """
    output: dict[str, dict[str, str]] = {"jockey": {}, "trainer": {}}
    for row in iter_nodes(table, "tr"):
        ancestor = row.parent
        while ancestor is not None and ancestor.tag != "table":
            ancestor = ancestor.parent
        if ancestor is not table:
            continue
        cells = [cell for cell in direct_cells(row) if cell.tag == "td"]
        for person_type, pattern in _PERSON_LINK_PATTERNS.items():
            anchors = [
                anchor for cell in cells for anchor in iter_nodes(cell, "a")
                if pattern.fullmatch(anchor.attrs.get("href", ""))
            ]
            if len(anchors) != 1:
                continue
            match = pattern.fullmatch(anchors[0].attrs["href"])
            assert match is not None
            person_id, display = match.group(1), node_text_raw(anchors[0])
            old = output[person_type].get(person_id)
            if old is not None and old != display:
                raise ValueError(f"OFFICIAL_CARD_PERSON_COMPACT_TOKEN_CONFLICT:{person_type}:{person_id}")
            output[person_type][person_id] = display
    return output


def parse_official_card_person_category_context(html: str, *, identity: dict[str, Any]) -> dict[int, dict[str, dict[str, str]]]:
    """Extract exact official-ID → V1-display evidence from a pre-race card.

    The official page shows the current registered person name in the entry
    table and, separately, its compact current-card display.  Both anchors
    carry the same official person ID.  This function only preserves those
    two displayed strings; it does not shorten, strip, or otherwise normalize
    either person name.
    """
    root = parse_html(html)
    entries = [
        table for table in iter_nodes(root, "table")
        if "馬番" in [node_text(cell) for cell in iter_nodes(table, "th")]
        and any("騎手名" in node_text(cell) for cell in iter_nodes(table, "th"))
        and any("調教師" in node_text(cell) for cell in iter_nodes(table, "th"))
        and any(re.fullmatch(r"/uma_info/\d+\.do", node.attrs.get("href", "")) for node in iter_nodes(table, "a"))
    ]
    compacts = [
        table for table in iter_nodes(root, "table")
        if "馬番" in [node_text(cell) for cell in iter_nodes(table, "th")]
        and any("騎手 (所属) 負担 調教師 (所属)" in node_text(cell) for cell in iter_nodes(table, "th"))
        and any(re.fullmatch(r"/uma_info/\d+\.do", node.attrs.get("href", "")) for node in iter_nodes(table, "a"))
    ]
    if not entries:
        raise ValueError("OFFICIAL_CARD_PERSON_ENTRY_TABLE_UNRESOLVED")
    if not compacts:
        raise ValueError("OFFICIAL_CARD_PERSON_COMPACT_TABLE_UNRESOLVED")
    # Prefer the actual roster table over an empty responsive wrapper or a
    # lower-page historical detail table.  The exact ID pairing below is the
    # semantic guard, not a layout assumption.
    entry_rows = sorted((_card_person_rows(table) for table in entries), key=len, reverse=True)
    compact_tokens = sorted((_card_person_tokens(table) for table in compacts), key=lambda rows: sum(len(value) for value in rows.values()), reverse=True)
    registered = entry_rows[0]
    # The official card can retain an excluded runner while the displayed
    # field-size is the starter count.  R6 established that the roster event
    # must not be silently discarded, so require matching card tables and at
    # least the declared starter count; the caller later reconciles every
    # stored race-runner key exactly.
    expected = int(identity["field_size"])
    if len(registered) < expected:
        raise ValueError(f"OFFICIAL_CARD_PERSON_CONTEXT_COUNT:{len(registered)}:{expected}")
    required_ids = {kind: {person[kind]["official_person_id"] for person in registered.values()} for kind in _PERSON_LINK_PATTERNS}
    legacy = next((tokens for tokens in compact_tokens if all(required_ids[kind].issubset(tokens[kind]) for kind in required_ids)), None)
    if legacy is None:
        raise ValueError("OFFICIAL_CARD_PERSON_COMPACT_TOKEN_COVERAGE_UNRESOLVED")
    output: dict[int, dict[str, dict[str, str]]] = {}
    for horse_number, names in registered.items():
        output[horse_number] = {}
        for person_type, current in names.items():
            token = legacy[person_type].get(current["official_person_id"])
            if not current["display"] or not token:
                raise ValueError(f"OFFICIAL_CARD_PERSON_DISPLAY_MISSING:{horse_number}:{person_type}")
            output[horse_number][person_type] = {
                "official_person_id": current["official_person_id"],
                "registered_person_name": current["display"],
                "v1_legacy_token": token,
            }
    return output


def parse_official_pedigree_identity_card(html: str, *, identity: dict[str, Any], enforce_affiliation_allowlist: bool = True) -> list[dict[str, Any]]:
    """Extract the only approved static pedigree fallback tuple from a card.

    The parser deliberately reads only the runner's dedicated pedigree/name
    cell in the official detailed-card table.  It does not read outcome,
    jockey, trainer, owner, odds, or performance cells.  Every tuple component
    is required exactly as displayed; no whitespace repair, fuzzy matching, or
    name-only fallback is performed here.
    """
    root = parse_html(html)
    output: dict[int, dict[str, Any]] = {}
    for row in iter_nodes(root, "tr"):
        cells = [cell for cell in direct_cells(row) if cell.tag == "td"]
        # The responsive detailed-card row has this dedicated static pedigree
        # cell.  Other display copies may contain a horse-name cell but no
        # stable `data-num`, so they are intentionally not a fallback.
        pedigree_cells = [cell for cell in cells if _class_has(cell, "pr-umaName-textRound")]
        if len(pedigree_cells) != 1:
            continue
        numbers = [int(cell.attrs["data-num"]) for cell in cells if re.fullmatch(r"\d+", cell.attrs.get("data-num", ""))]
        if len(numbers) != 1:
            raise ValueError("OFFICIAL_PEDIGREE_CARD_HORSE_NUMBER_UNRESOLVED")
        cell, horse_number = pedigree_cells[0], numbers[0]
        name_nodes = [node for node in iter_nodes(cell, "span") if _class_has(node, "nk23_u-text16")]
        sire_nodes = [node for node in iter_nodes(cell, "p") if _class_has(node, "nk23_u-text12")]
        detail_nodes = [node_text(node) for node in iter_nodes(cell, "p") if _class_has(node, "nk23_u-text10")]
        if len(name_nodes) != 1 or len(sire_nodes) != 1 or len(detail_nodes) < 3:
            raise ValueError(f"BLOCK_IDENTITY_PEDIGREE_MISSING_FIELD:{horse_number}")
        horse_name_raw, sire = node_text(name_nodes[0]), node_text(sire_nodes[0])
        card_affiliation_prefix, horse_name_exact = split_official_card_affiliation_prefix(
            horse_name_raw, enforce_allowlist=enforce_affiliation_allowlist
        )
        # The first text10 paragraph is sex/color/birth-date display.  The next
        # two are the static dam and exact full-width parenthesized damsire.
        dam, damsire_raw = detail_nodes[1], detail_nodes[2]
        damsire = re.fullmatch(r"（([^（）]+)）", damsire_raw)
        if not horse_name_raw or not horse_name_exact or not sire or not dam or damsire is None:
            raise ValueError(f"BLOCK_IDENTITY_PEDIGREE_MISSING_FIELD:{horse_number}")
        anchors = [node for node in iter_nodes(cell, "a") if re.fullmatch(r"/uma_info/(\d+)\.do", node.attrs.get("href", ""))]
        if len(anchors) > 1:
            raise ValueError(f"OFFICIAL_PEDIGREE_CARD_DETAIL_ID_AMBIGUOUS:{horse_number}")
        official_horse_id = None
        if anchors:
            match = re.fullmatch(r"/uma_info/(\d+)\.do", anchors[0].attrs["href"])
            official_horse_id = match.group(1) if match else None
        item = {"horse_number": horse_number, "card_horse_name_raw": horse_name_raw,
                "card_affiliation_prefix": card_affiliation_prefix, "card_horse_name_identity": horse_name_exact,
                "horse_name_exact": horse_name_exact, "sire": sire,
                "dam": dam, "damsire": damsire.group(1), "official_horse_id": official_horse_id,
                "official_horse_url": urllib.parse.urljoin("https://www.nankankeiba.com", anchors[0].attrs["href"]) if anchors else None}
        if horse_number in output and output[horse_number] != item:
            raise ValueError(f"OFFICIAL_PEDIGREE_CARD_DUPLICATE_CONFLICT:{horse_number}")
        output[horse_number] = item
    if len(output) < int(identity["field_size"]):
        raise ValueError(f"OFFICIAL_PEDIGREE_CARD_RUNNER_COUNT_UNRESOLVED:{len(output)}:{identity['field_size']}")
    return [output[number] for number in sorted(output)]


def horse_detail_identity_name(raw_name: str) -> tuple[str, str | None]:
    """Apply the sole approved horse-detail display annotation rule."""
    if raw_name.endswith("（抹消）"):
        name = raw_name[:-len("（抹消）")]
        if not name:
            raise ValueError("official horse detail full identity unavailable")
        return name, "DEREGISTERED"
    if re.search(r"（[^（）]+）$", raw_name):
        raise ValueError("BLOCK_SOURCE_NAME_ANNOTATION_UNRESOLVED")
    return raw_name, None


def parse_official_horse_detail(html: str, *, official_horse_id: str) -> dict[str, str]:
    """Parse official detail identity while preserving its display-name raw form.

    `（抹消）` is the sole approved terminal administrative annotation.  It is
    never removed from raw provenance, and no other punctuation/name repair is
    allowed for identity comparison.
    """
    root = parse_html(html)
    birth_date = None
    for row in iter_nodes(root, "tr"):
        cells = direct_cells(row)
        if len(cells) >= 2 and node_text(cells[0]).strip() == "生年月日":
            value = node_text(cells[1]).strip()
            match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value)
            if match:
                birth_date = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                break
    title = next((node_text(node).strip() for node in iter_nodes(root, "h2") if node.attrs.get("id") == "tl-prof"), "")
    raw_name = title.strip()
    identity_name, registration_status = horse_detail_identity_name(raw_name)
    if not birth_date or not raw_name or not identity_name:
        raise ValueError("official horse detail full identity unavailable")
    return {"official_horse_id": official_horse_id, "horse_detail_name_raw": raw_name,
            "horse_detail_name_identity": identity_name, "horse_name_exact": identity_name,
            "birth_date": birth_date, "horse_registration_status": registration_status}


def resolve_odds_urls(html: str, base_url: str) -> dict[str, str]:
    root = parse_html(html); found: dict[str, str] = {}
    labels = {"WIN": "単勝・複勝", "WIDE": "馬複・ワイド", "TRIO": "三連複"}
    for anchor in iter_nodes(root, "a"):
        label = node_text(anchor)
        href = anchor.attrs.get("href", "")
        for kind, marker in labels.items():
            if marker in label and href.startswith("/odds/"):
                found[kind] = urllib.parse.urljoin(base_url, href)
    if set(found) != set(labels):
        raise ValueError(f"required odds links missing from DOM: {sorted(set(labels) - set(found))}")
    return found


def resolve_initial_odds_url(html: str, base_url: str) -> str:
    """Discover the generic odds entry from the race page; do not construct suffixes."""
    root = parse_html(html)
    for anchor in iter_nodes(root, "a"):
        href = anchor.attrs.get("href", "")
        if anchor.attrs.get("data-menu") == "odds" and href.startswith("/odds/"):
            return urllib.parse.urljoin(base_url, href)
    raise ValueError("official odds entry link missing from race-page DOM")


def resolve_result_url(html: str, base_url: str) -> str:
    """Use only an explicit official result anchor present in the registered race page."""
    for anchor in iter_nodes(parse_html(html), "a"):
        href = anchor.attrs.get("href", "")
        if anchor.attrs.get("data-menu") == "result" and href.startswith("/result/"):
            return urllib.parse.urljoin(base_url, href)
    raise ValueError("official result link missing from registered race page")


def _table_headers(table: Node) -> list[str]:
    return [node_text(cell) for cell in iter_nodes(table, "th")]


def parse_official_result_components(html: str, *, identity: dict[str, Any]) -> dict[str, Any]:
    """Parse exact-race result components without asserting all payout types.

    This is the source-component primitive for staged result completeness.  It
    intentionally preserves the final collector's existing row grammar; the
    caller decides whether the available components satisfy finality.
    """
    resolved = parse_race_identity(html)
    if any(resolved[key] != identity[key] for key in ("race_date", "venue", "race_number")):
        raise ValueError("RESULT_IDENTITY_FAILED")
    root = parse_html(html)
    runner_table = next((table for table in iter_nodes(root, "table") if {"着", "馬番"} <= set(_table_headers(table))), None)
    if runner_table is None:
        raise ValueError("RESULT_NOT_AVAILABLE: runner result table missing")
    runners: list[dict[str, Any]] = []
    seen_runners: set[int] = set()
    for row in iter_nodes(runner_table, "tr"):
        values = [node_text(cell) for cell in direct_cells(row) if cell.tag == "td"]
        if len(values) < 3 or not re.fullmatch(r"\d+", values[2]):
            continue
        horse = int(values[2])
        if horse in seen_runners:
            raise ValueError("duplicate official runner")
        seen_runners.add(horse)
        finish = int(values[0]) if re.fullmatch(r"\d+", values[0]) else None
        runners.append({"horse_number": horse, "finish_position": finish, "result_status": "FINISHED" if finish is not None else None, "raw_status": values[0], "parse_status": "PARSED" if finish is not None else "FINISH_POSITION_UNRESOLVED"})
    if not runners:
        raise ValueError("RESULT_NOT_AVAILABLE: no runner rows")
    payouts: list[dict[str, Any]] = []
    target = {"単勝": "WIN", "ワイド": "WIDE", "三連複": "TRIO"}
    for table in iter_nodes(root, "table"):
        ancestor = table.parent
        desktop = False
        while ancestor is not None:
            if "pc" in ancestor.attrs.get("class", "").split():
                desktop = True; break
            ancestor = ancestor.parent
        if not desktop:
            continue
        first_header = [node_text(cell) for row in iter_nodes(table, "tr") for cell in direct_cells(row) if cell.tag == "th"]
        groups = [item for item in first_header if item in target]
        if not groups:
            continue
        for row in iter_nodes(table, "tr"):
            values = [node_text(cell) for cell in direct_cells(row) if cell.tag == "td"]
            if not values:
                continue
            for index, header in enumerate(groups):
                offset = index * 3
                if len(values) < offset + 2:
                    continue
                combo, payout = values[offset], values[offset + 1]
                if combo == "-" or payout == "-":
                    continue
                if header not in target or not re.fullmatch(r"\d+(?:-\d+){0,2}", combo) or not re.fullmatch(r"[\d,]+", payout):
                    continue
                # A displayed payout row is usable only for its own ticket
                # family.  Incomplete/malformed rows remain unavailable for
                # staged assessment; strict final persistence still requires
                # every ticket family.
                parts = combo.split("-")
                expected_parts = {"単勝": 1, "ワイド": 2, "三連複": 3}[header]
                if len(parts) != expected_parts or len(set(parts)) != len(parts):
                    continue
                payouts.append({"ticket_type": target[header], "combination_raw": combo, "payout_raw": payout, "payout_amount": int(payout.replace(",", "")), "payout_unit": None, "parse_status": "PAYOUT_UNIT_UNRESOLVED"})
    selected = [item for item in payouts if item["ticket_type"] in {"WIN", "WIDE", "TRIO"}]
    return {"identity": resolved, "runners": runners, "payouts": selected,
            "payout_types": sorted({item["ticket_type"] for item in selected})}


def parse_official_result(html: str, *, identity: dict[str, Any]) -> dict[str, Any]:
    """Parse only settled official runner results and WIN/WIDE/TRIO payout rows.

    The official `成績・払戻金` page is accepted as final only when all required
    runner and target-payout tables are present and internally parseable.
    """
    components = parse_official_result_components(html, identity=identity)
    selected = components["payouts"]
    present = set(components["payout_types"])
    if present != {"WIN", "WIDE", "TRIO"}:
        raise ValueError("RESULT_AVAILABLE_NOT_FINAL: required WIN/WIDE/TRIO payout tables incomplete")
    return {"finality_status": "RESULT_OFFICIAL_FINAL", "runners": components["runners"], "payouts": selected,
            "finality_evidence": "OFFICIAL_RESULT_AND_PAYOUT_TABLES_COMPLETE"}


def parse_official_refund_horse_numbers(html: str) -> dict[str, Any]:
    """Parse only an explicit official payout-note refund declaration.

    This is deliberately separate from :func:`parse_official_result`: a final
    result/payout table can be valid while settlement still needs to know that
    a recommended ticket was refunded.  The sole approved live-corpus grammar
    is the displayed payout-note token ``返還：3,9号馬``.  Any other appearance
    of ``返還`` is retained for review rather than inferred as a ticket refund.
    """
    root = parse_html(html)
    notes: list[str] = []
    for table in iter_nodes(root, "table"):
        ancestor = table.parent
        desktop = False
        while ancestor is not None:
            if "pc" in ancestor.attrs.get("class", "").split():
                desktop = True
                break
            ancestor = ancestor.parent
        if not desktop or "備考" not in _table_headers(table):
            continue
        for row in iter_nodes(table, "tr"):
            for cell in direct_cells(row):
                value = node_text(cell)
                if "返還" in value:
                    notes.append(value)
    if not notes:
        return {"status": "NO_REFUND", "horse_numbers": [], "raw_notes": []}
    horses: set[int] = set()
    for note in notes:
        match = re.fullmatch(r"返還：([0-9]+(?:,[0-9]+)*)号馬", note)
        if match is None:
            return {"status": "REFUND_REVIEW_REQUIRED", "horse_numbers": [], "raw_notes": notes}
        values = [int(value) for value in match.group(1).split(",")]
        if any(value <= 0 for value in values):
            return {"status": "REFUND_REVIEW_REQUIRED", "horse_numbers": [], "raw_notes": notes}
        horses.update(values)
    return {"status": "REFUND_HORSE_NUMBERS", "horse_numbers": sorted(horses), "raw_notes": notes}


def _official_result_status_vocabulary() -> dict[str, Any]:
    """Load the deliberately narrow, audited official finish-display map.

    This is a JSON-compatible YAML file so the repository does not introduce a
    second YAML parser dependency.  It is intentionally *not* a general racing
    vocabulary: an unknown nonnumeric finish display is a hard block.
    """
    payload = json.loads(_RESULT_STATUS_VOCABULARY.read_text(encoding="utf-8"))
    mappings = payload.get("finish_display_mappings")
    if not isinstance(mappings, dict):
        raise RuntimeError("OFFICIAL_RESULT_STATUS_VOCABULARY_INVALID")
    return mappings


def parse_history_result_raw_rows(html: str, *, identity: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return structural official result rows before outcome-status promotion.

    The helper is shared by the R10 bounded vocabulary audit and the live-delta
    parser.  It retains exact display tokens; it does not classify them.
    """
    resolved = parse_race_identity(html)
    if any(resolved[key] != identity[key] for key in ("race_date", "venue", "race_number")):
        raise ValueError("RESULT_IDENTITY_FAILED")
    root = parse_html(html)
    table = next((value for value in iter_nodes(root, "table") if {"着", "馬番", "タイム", "上がり3F"} <= set(_table_headers(value))), None)
    if table is None:
        raise ValueError("RESULT_NOT_AVAILABLE: history runner table missing")
    headers = _table_headers(table)
    required = {"着", "枠", "馬番", "馬名", "性齢", "負担", "馬体重", "増減", "騎手", "調教師", "タイム", "着差", "上がり3F", "コーナー通過順"}
    if not required <= set(headers):
        raise ValueError("BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_result_runner_table")
    index = {name: headers.index(name) for name in required}
    runners: list[dict[str, Any]] = []
    for row in iter_nodes(table, "tr"):
        values = [node_text(cell) for cell in direct_cells(row) if cell.tag == "td"]
        if len(values) <= max(index.values()) or not re.fullmatch(r"\d+", values[index["馬番"]]):
            continue
        finish_raw = values[index["着"]]
        age_sex = re.fullmatch(r"([牡牝騙])(\d+)", values[index["性齢"]])
        weight = re.search(r"(\d+)", values[index["馬体重"]])
        change = re.search(r"([＋+－\-±])\s*(\d+)", values[index["増減"]])
        finish_time_raw = values[index["タイム"]].strip()
        margin_raw = values[index["着差"]].strip()
        # The frozen historical normalizer represents unavailable official
        # performance cells as NULL, not the display placeholder "-".
        if finish_time_raw == "-":
            finish_time_raw = None
        if margin_raw == "-":
            margin_raw = None
        runners.append({
            "horse_number": int(values[index["馬番"]]), "frame_number": int(values[index["枠"]]) if values[index["枠"]].isdigit() else None,
            "horse_name_exact": values[index["馬名"]].strip(), "sex": age_sex.group(1) if age_sex else None,
            "assigned_weight": float(values[index["負担"]]) if re.fullmatch(r"\d+(?:\.\d+)?", values[index["負担"]]) else None,
            "body_weight": int(weight.group(1)) if weight else None,
            "body_weight_change": (1 if change and change.group(1) in {"+", "＋"} else -1 if change and change.group(1) in {"-", "－"} else 0) * int(change.group(2)) if change else None,
            "jockey": values[index["騎手"]].strip() or None, "trainer": values[index["調教師"]].strip() or None,
            "finish_position_raw": finish_raw,
            "finish_time_raw": finish_time_raw or None, "margin_raw": margin_raw or None,
            "last_3f": float(values[index["上がり3F"]]) if re.fullmatch(r"\d+(?:\.\d+)?", values[index["上がり3F"]]) else None,
        })
    return resolved, runners


def _promote_official_finish_displays(runners: list[dict[str, Any]]) -> None:
    """Promote only audited exact finish displays to frozen result semantics."""
    mappings = _official_result_status_vocabulary()
    previous_finish: int | None = None
    previous_time: str | None = None
    for runner in runners:
        raw = str(runner["finish_position_raw"])
        if raw.isdigit():
            finish = int(raw)
            if finish <= 0:
                raise ValueError("BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_result_status_semantics")
            runner["finish_position"] = finish
            runner["result_status"] = "FINISHED"
            previous_finish, previous_time = finish, runner["finish_time_raw"]
            continue
        mapping = mappings.get(raw)
        if mapping is None:
            runner["finish_position"] = None
            runner["result_status"] = "RAW_FINISH_STATUS_MISSING"
            continue
        if mapping.get("normalized_result_status") != "FINISHED" or mapping.get("finish_position_rule") != "REPEAT_IMMEDIATELY_PRECEDING_NUMERIC_FINISH":
            raise ValueError("OFFICIAL_RESULT_STATUS_VOCABULARY_INVALID")
        if runner["margin_raw"] != mapping.get("required_margin_raw") or previous_finish is None:
            raise ValueError("BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_result_status_semantics")
        if mapping.get("required_same_finish_time") and (runner["finish_time_raw"] is None or runner["finish_time_raw"] != previous_time):
            raise ValueError("BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_result_status_semantics")
        # This is the official tied rank, not a fabricated terminal position.
        runner["finish_position"] = previous_finish
        runner["result_status"] = "FINISHED"


def parse_history_result_fields(html: str, *, identity: dict[str, Any]) -> dict[str, Any]:
    """Parse the final-result fields needed to advance strict-as-of history."""
    resolved, runners = parse_history_result_raw_rows(html, identity=identity)
    _promote_official_finish_displays(runners)
    # `field_size` is the final starter count.  The official final table can
    # additionally retain a nonstarter/excluded runner; preserve that runner
    # for raw-status semantics while requiring the count of actual finishers to
    # equal the displayed final field size.
    # M07 owns the outcome vocabulary.  Reuse it exactly: a `競走中止` runner
    # started but has no valid numeric finish; cancellation/exclusion rows are
    # not starters; every other missing-finish combination remains a block.
    from src.audit.p2_m07_target_universe import starter_status
    statuses = [starter_status(row["result_status"], row["margin_raw"], row["finish_position"]) for row in runners]
    if "UNRESOLVED_OUTCOME_STATUS" in statuses:
        raise ValueError("BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_result_status_semantics")
    starter_count = sum(status in {"STARTER_VALID_FINISH", "STARTER_NO_VALID_FINISH"} for status in statuses)
    if starter_count != identity["field_size"] or len({row["horse_number"] for row in runners}) != len(runners):
        raise ValueError("RESULT_HISTORY_RUNNER_ROSTER_UNRESOLVED")
    text = node_text(parse_html(html))
    lap = re.search(r"ハロンタイム\s*([0-9.\-\s]+)", text)
    lap_values = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", lap.group(1))] if lap else []
    if not lap_values:
        raise ValueError("BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_lap_times_json")
    going = re.search(r"馬場\s*[:：]\s*([芝ダ]\S+)", text)
    weather = re.search(r"天候\s*[:：]\s*(\S+)", text)
    return {"identity": resolved, "runners": runners, "weather": weather.group(1) if weather else None,
            "going": going.group(1) if going else None, "lap_times": lap_values,
            "final_3f": sum(lap_values[-3:]) if len(lap_values) >= 3 else None,
            "final_4f": sum(lap_values[-4:]) if len(lap_values) >= 4 else None}


def parse_win_odds(html: str) -> list[dict[str, Any]]:
    table = following_table(parse_html(html), "単勝・複勝")
    output = []
    for row in iter_nodes(table, "tr"):
        cells = direct_cells(row)
        tds = [node_text(cell) for cell in cells if cell.tag == "td"]
        if len(tds) < 3 or not re.fullmatch(r"\d+", tds[0]):
            continue
        odds = re.search(r"\d+(?:\.\d+)?", tds[2])
        if odds:
            output.append({"horse_number": int(tds[0]), "odds_value": float(odds.group(0))})
    return output


def _grid_entries(table: Node, header_parser):
    headers: dict[int, Any] = {}; entries = []
    for row in iter_nodes(table, "tr"):
        position = 0; cells = direct_cells(row); index = 0
        while index < len(cells):
            cell = cells[index]; value = node_text(cell); colspan = int(cell.attrs.get("colspan", "1") or "1")
            header = header_parser(value) if cell.tag == "th" and colspan == 2 else None
            if header is not None:
                headers[position] = header; position += colspan; index += 1; continue
            if cell.tag == "th" and colspan == 1 and position in headers and index + 1 < len(cells) and cells[index + 1].tag == "td":
                entries.append((headers[position], value, node_text(cells[index + 1])))
                position += 2; index += 2; continue
            position += colspan; index += 1
    return entries


def parse_wide_odds(html: str) -> list[dict[str, Any]]:
    table = following_table(parse_html(html), "ワイド")
    parsed = _grid_entries(table, lambda value: int(value) if re.fullmatch(r"\d+", value) else None)
    output = {}
    for first, second_raw, odds_raw in parsed:
        if not re.fullmatch(r"\d+", second_raw):
            continue
        values = re.findall(r"\d+(?:\.\d+)?", odds_raw)
        if len(values) != 2:
            continue
        # Preserve the exact displayed tokens as provenance.  Existing callers
        # continue to use the numeric lower/upper fields; the raw tokens are
        # an additive source fact needed by the frozen display-precision
        # uncertainty contract and are never used by the operational policy.
        second = int(second_raw); low, high = float(values[0]), float(values[1])
        key = "-".join(str(item) for item in sorted((first, second)))
        if first != second:
            output[key] = {
                "horse_number_1": min(first, second), "horse_number_2": max(first, second),
                "lower_odds": low, "upper_odds": high,
                "lower_odds_raw": values[0], "upper_odds_raw": values[1],
                "normalized_combination_key": key,
            }
    return [output[key] for key in sorted(output, key=lambda item: tuple(map(int, item.split("-"))))]


def parse_trio_odds(html: str) -> list[dict[str, Any]]:
    root = parse_html(html); output = {}
    for table in iter_nodes(root, "table"):
        headers = [node_text(cell) for cell in iter_nodes(table, "th") if cell.attrs.get("colspan") == "2"]
        if not any(re.fullmatch(r"\d+-\d+", value) for value in headers):
            continue
        parsed = _grid_entries(table, lambda value: tuple(map(int, value.split("-"))) if re.fullmatch(r"\d+-\d+", value) else None)
        for pair, third_raw, odds_raw in parsed:
            if not re.fullmatch(r"\d+", third_raw):
                continue
            odds = re.search(r"\d+(?:\.\d+)?", odds_raw)
            if odds is None:
                continue
            combo = tuple(sorted((*pair, int(third_raw))))
            if len(set(combo)) == 3:
                key = "-".join(map(str, combo))
                output[key] = {"horse_number_1": combo[0], "horse_number_2": combo[1], "horse_number_3": combo[2], "odds_value": float(odds.group(0)), "normalized_combination_key": key}
    return [output[key] for key in sorted(output, key=lambda item: tuple(map(int, item.split("-"))))]


def extract_source_displayed_at(html: str, race_date: str) -> dict[str, str | None]:
    match = re.search(r"(\d{1,2}:\d{2})\s*現在", node_text(parse_html(html)))
    # A time-only display cannot safely be associated with the race date.
    return {"source_displayed_time_raw": match.group(1) if match else None, "source_displayed_at": None}


def extract_http_cache_metadata(result: FetchResult) -> dict[str, Any]:
    headers = {key.lower(): value for key, value in result.headers.items()}
    return {"request_started_at": result.request_started_at, "captured_at": result.captured_at, "final_url": result.final_url, "redirect_chain": result.redirect_chain, "http_date": headers.get("date"), "age": headers.get("age"), "cache_control": headers.get("cache-control"), "etag": headers.get("etag"), "last_modified": headers.get("last-modified"), "expires": headers.get("expires"), "status_code": result.status_code, "content_type": headers.get("content-type"), "content_length": headers.get("content-length")}


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__(); self.chain: list[dict[str, Any]] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        self.chain.append({"status_code": code, "from_url": req.full_url, "location": headers.get("Location"), "to_url": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_race_page(url: str, timeout_seconds: int = 30) -> FetchResult:
    host = urllib.parse.urlparse(url).hostname
    if host not in OFFICIAL_HOSTS:
        raise ValueError("Nankan official adapter only accepts official host URLs")
    recorder = _RedirectRecorder(); opener = urllib.request.build_opener(recorder)
    started = utc_now()
    request = urllib.request.Request(url, headers={"User-Agent": "Phase2NankanFixtureAdapter/1.0", "Cache-Control": "no-cache", "Pragma": "no-cache"})
    with opener.open(request, timeout=timeout_seconds) as response:  # nosec B310: explicit official fixture fetch
        raw = response.read(); captured = utc_now()
        return FetchResult(url, started, captured, response.geturl(), recorder.chain, int(response.status), dict(response.headers.items()), raw)


def fetch_odds_page(url: str, timeout_seconds: int = 30) -> FetchResult:
    return fetch_race_page(url, timeout_seconds)
