"""Foreground, one-race live freshness probe. It performs no model work."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import (
    DEFAULT_DB, append_manifest, archive_bytes, canonical_race_key, connect,
    initialize_database, record_capture, record_market_snapshot, register_race,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / "outputs/live_freshness"
MARKS = (("T20", 20, "INITIAL"), ("T15", 15, "PRIMARY_CANDIDATE"), ("T10", 10, "SECONDARY"), ("T05", 5, "SECONDARY"))


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class Fetcher(Protocol):
    def fetch(self, url: str, timeout_seconds: int) -> official.FetchResult: ...


class OfficialFetcher:
    def fetch(self, url: str, timeout_seconds: int) -> official.FetchResult:
        return official.fetch_race_page(url, timeout_seconds)


@dataclass
class ProbeConfig:
    entry_url: str
    db_path: Path = DEFAULT_DB
    output_root: Path = OUTPUT_ROOT
    raw_root: Path | None = None
    request_timeout_seconds: int = 30
    max_initial_wait_seconds: int = 45 * 60
    marks: tuple[tuple[str, int, str], ...] = MARKS


def iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must provide timezone-aware time")
    return value.astimezone(timezone.utc).isoformat()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def mark_times(post_time: datetime, marks: tuple[tuple[str, int, str], ...] = MARKS) -> dict[str, datetime]:
    return {name: post_time - timedelta(minutes=minutes) for name, minutes, _ in marks}


def parse_post(identity: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(f"{identity['race_date']}T{identity['scheduled_post_time_local']}:00+09:00").astimezone(timezone.utc)


def resolve_bootstrap_response(entry_url: str, response: official.FetchResult) -> tuple[dict[str, Any], datetime]:
    """Parse a fetched entry response once for the probe bootstrap.

    Kept separate from the network fetch so fixture and hotfix verification can
    exercise the exact bootstrap identity path without issuing a second request.
    """
    html = official.decode_html(response.raw, response.headers.get("Content-Type"))
    identity = official.resolve_race(entry_url, html)
    return identity, parse_post(identity)


def snapshot_summary(rows: list[dict[str, Any]], expected_keys: set[str]) -> dict[str, Any]:
    keys = [row.get("normalized_combination_key") or f"{row['horse_number']:02d}" for row in rows]
    actual = set(keys)
    return {"expected": len(expected_keys), "parsed": len(rows), "missing": sorted(expected_keys - actual), "duplicate": len(keys) - len(actual), "complete": len(rows) == len(expected_keys) and actual == expected_keys}


def expected_keys(active: list[int]) -> tuple[set[str], set[str], set[str]]:
    win = {f"{item:02d}" for item in active}
    wide = {f"{a}-{b}" for index, a in enumerate(active) for b in active[index + 1:]}
    trio = {f"{a}-{b}-{c}" for ai, a in enumerate(active) for bi, b in enumerate(active[ai + 1:], ai + 1) for c in active[bi + 1:]}
    return win, wide, trio


def compare_quotes(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> int | None:
    if not previous or not current:
        return None
    def mapping(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {row.get("normalized_combination_key") or f"{row['horse_number']:02d}": row for row in rows}
    before, after = mapping(previous), mapping(current)
    return sum(before.get(key) != after.get(key) for key in set(before) | set(after))


def compare_bodyweight(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> int | None:
    if not previous or not current:
        return None
    before = {row["horse_number"]: row for row in previous.get("runners", [])}
    after = {row["horse_number"]: row for row in current.get("runners", [])}
    return sum(before.get(key) != after.get(key) for key in set(before) | set(after))


class LiveFreshnessProbe:
    def __init__(self, config: ProbeConfig, *, clock: Clock | None = None, fetcher: Fetcher | None = None, printer: Callable[[str], None] | None = print):
        self.config, self.clock, self.fetcher, self.printer = config, clock or SystemClock(), fetcher or OfficialFetcher(), printer

    def _say(self, message: str) -> None:
        if self.printer:
            self.printer(message)

    def _fetch(self, url: str) -> official.FetchResult:
        before = self.clock.monotonic()
        result = self.fetcher.fetch(url, self.config.request_timeout_seconds)
        if self.clock.monotonic() - before > self.config.request_timeout_seconds:
            raise TimeoutError("FETCH_TIMEOUT")
        return result

    def _capture_source(self, conn: sqlite3.Connection, race_id: str, race_key: str, source_type: str, kind: str, result: official.FetchResult) -> tuple[str, dict[str, Any]]:
        capture_id, raw_path, size = archive_bytes(source_type, race_key, result.raw, result.captured_at, result.headers.get("Content-Type"), self.config.raw_root)
        digest = sha256(result.raw); metadata = official.extract_http_cache_metadata(result)
        record_capture(conn, race_registry_id=race_id, source_type=source_type, source_name="NANKAN_OFFICIAL_LIVE_FRESHNESS_PROBE", source_reference=result.final_url, submitted_url=result.requested_url, requested_at=result.request_started_at, captured_at=result.captured_at, source_published_at=None, http_status=result.status_code, content_type=result.headers.get("Content-Type"), encoding=None, raw_archive_path_value=raw_path, raw_sha256=digest, response_size_bytes=size, capture_status="COLLECTED_OK", collector_version="p2-a02b2-live-freshness-v1", parser_version="nankan-official-fixture-v1", notes=json.dumps({"engineering_status": "LIVE_FRESHNESS_TEST", "capture_kind": kind, "http_cache_metadata": metadata}, ensure_ascii=False, sort_keys=True), capture_id=capture_id)
        append_manifest(capture_id=capture_id, source_type=source_type, race_key=race_key, captured_at=result.captured_at, source_reference=result.final_url, raw_path=raw_path, size_bytes=size, sha256=digest, collector_version="p2-a02b2-live-freshness-v1", parser_version="nankan-official-fixture-v1", status="LIVE_FRESHNESS_TEST")
        return capture_id, {"raw_sha256": digest, "raw_path": raw_path, "response_size": size, "http": metadata}

    def _attempt_capture(self, mark: str, role: str, identity_bootstrap: dict[str, Any], post: datetime) -> dict[str, Any]:
        capture: dict[str, Any] = {"attempted": True, "status": "CAPTURE_FAILED", "requested_at": iso(self.clock.now()), "captured_at": None, "minutes_to_post": None, "race_identity": None, "bodyweight_summary": None, "win_summary": None, "wide_summary": None, "trio_summary": None, "http_metadata": {}, "raw_hashes": {}, "errors": [], "engineering_status": "LIVE_FRESHNESS_TEST", "snapshot_role": role, "target_decision_time": "T-15_ENGINEERING_CANDIDATE" if mark == "T15" else "LIVE_FRESHNESS_TEST"}
        initialize_database(self.config.db_path); conn = connect(self.config.db_path)
        try:
            entry = self._fetch(self.config.entry_url)
            entry_html = official.decode_html(entry.raw, entry.headers.get("Content-Type")); identity = official.resolve_race(self.config.entry_url, entry_html)
            if identity != identity_bootstrap:
                raise ValueError("RACE_IDENTITY_CHANGED_DURING_PROBE")
            now = self.clock.now(); capture["captured_at"] = entry.captured_at; capture["minutes_to_post"] = (post - now).total_seconds() / 60; capture["race_identity"] = identity
            race_key = canonical_race_key(identity["race_date"], identity["venue"], identity["race_number"])
            race_id = register_race(conn, race_date=identity["race_date"], venue=identity["venue"], race_number=identity["race_number"], scheduled_post_time=iso(post), scheduled_post_time_source="NANKAN_OFFICIAL_LIVE_FRESHNESS_PROBE", scheduled_post_time_captured_at=entry.captured_at, eligibility_status="LIVE_FRESHNESS_TEST", collection_status="COLLECTING", notes="T-15 remains ENGINEERING_CANDIDATE and NOT_FROZEN.")
            _, entry_meta = self._capture_source(conn, race_id, race_key, "BODY_WEIGHT", "ENTRY", entry); capture["raw_hashes"]["ENTRY"] = entry_meta["raw_sha256"]; capture["http_metadata"]["ENTRY"] = entry_meta["http"]
            body = official.parse_bodyweight(entry_html, identity=identity, captured_at=entry.captured_at); capture["bodyweight"] = body
            active = sorted(row["horse_number"] for row in body["runners"] if row.get("scratch_status") not in {"SCRATCH", "CANCELLED"})
            capture["bodyweight_summary"] = {"expected": identity["field_size"], "parsed": len(body["runners"]), "active_runner_universe": active, "market_fields_in_curated_output": 0}
            win_url = official.resolve_initial_odds_url(entry_html, entry.final_url)
            win_page = self._fetch(win_url); win_html = official.decode_html(win_page.raw, win_page.headers.get("Content-Type")); odds_urls = official.resolve_odds_urls(win_html, win_page.final_url)
            pages = {"WIN": (win_page, win_html, win_url), "WIDE": (self._fetch(odds_urls["WIDE"]), None, odds_urls["WIDE"]), "TRIO": (self._fetch(odds_urls["TRIO"]), None, odds_urls["TRIO"])}
            pages["WIDE"] = (pages["WIDE"][0], official.decode_html(pages["WIDE"][0].raw, pages["WIDE"][0].headers.get("Content-Type")), pages["WIDE"][2])
            pages["TRIO"] = (pages["TRIO"][0], official.decode_html(pages["TRIO"][0].raw, pages["TRIO"][0].headers.get("Content-Type")), pages["TRIO"][2])
            rows = {"WIN": official.parse_win_odds(pages["WIN"][1]), "WIDE": official.parse_wide_odds(pages["WIDE"][1]), "TRIO": official.parse_trio_odds(pages["TRIO"][1])}
            capture["quotes"] = rows
            expected_win, expected_wide, expected_trio = expected_keys(active)
            summaries = {"WIN": snapshot_summary(rows["WIN"], expected_win), "WIDE": snapshot_summary(rows["WIDE"], expected_wide), "TRIO": snapshot_summary(rows["TRIO"], expected_trio)}
            capture["win_summary"], capture["wide_summary"], capture["trio_summary"] = summaries["WIN"], summaries["WIDE"], summaries["TRIO"]
            for kind in ("WIN", "WIDE", "TRIO"):
                page = pages[kind][0]; capture_id, meta = self._capture_source(conn, race_id, race_key, "MARKET", kind, page); capture["raw_hashes"][kind] = meta["raw_sha256"]; capture["http_metadata"][kind] = meta["http"]
                for row in rows[kind]:
                    key = row.get("normalized_combination_key") or f"{row['horse_number']:02d}"
                    record_market_snapshot(conn, race_registry_id=race_id, capture_id=capture_id, bet_type_code=kind, normalized_combination_key=key, captured_at=page.captured_at, scheduled_post_time=iso(post), snapshot_role=role, target_decision_time="T-15_ENGINEERING_CANDIDATE" if mark == "T15" else "LIVE_FRESHNESS_TEST", response_sha256=meta["raw_sha256"], availability_status="LIVE_FRESHNESS_TEST", quality_status="COMPLETE" if summaries[kind]["complete"] else "PARTIAL", odds_value=row.get("odds_value", row.get("lower_odds")), max_odds_value=row.get("upper_odds"), field_size=len(active), collector_version="p2-a02b2-live-freshness-v1", parser_version="nankan-official-fixture-v1", notes=json.dumps({"engineering_status": "LIVE_FRESHNESS_TEST", "mark": mark}, ensure_ascii=False))
            conn.commit()
            capture["status"] = "PASS" if all(summary["complete"] for summary in summaries.values()) else "PARTIAL"
        except Exception as exc:  # preserve the failure and continue with later marks
            capture["errors"].append({"code": type(exc).__name__, "message": str(exc)})
            try: conn.commit()
            except Exception: pass
        finally:
            conn.close()
        return capture

    def _comparison(self, captures: dict[str, dict[str, Any]]) -> dict[str, Any]:
        result = {"hash_changes": {}, "quote_change_counts": {}, "cache_header_changes": {}}
        previous = None
        for name, _, _ in self.config.marks:
            current = captures.get(name)
            if not current or current.get("status") not in {"PASS", "PARTIAL"}:
                previous = current if current else previous; continue
            if previous and previous.get("status") in {"PASS", "PARTIAL"}:
                result["hash_changes"][f"{previous['mark']}->{name}"] = {kind: previous.get("raw_hashes", {}).get(kind) != current.get("raw_hashes", {}).get(kind) for kind in {**previous.get("raw_hashes", {}), **current.get("raw_hashes", {})}}
                result["quote_change_counts"][f"{previous['mark']}->{name}"] = {"bodyweight": compare_bodyweight(previous.get("bodyweight"), current.get("bodyweight")), "WIN": None, "WIDE": None, "TRIO": None}
                for kind, key in (("WIN", "win_summary"), ("WIDE", "wide_summary"), ("TRIO", "trio_summary")):
                    result["quote_change_counts"][f"{previous['mark']}->{name}"][kind] = None if previous.get(key) is None or current.get(key) is None else compare_quotes(previous.get("quotes", {}).get(kind, []), current.get("quotes", {}).get(kind, []))
                result["cache_header_changes"][f"{previous['mark']}->{name}"] = {kind: {header: previous.get("http_metadata", {}).get(kind, {}).get(header) != current.get("http_metadata", {}).get(kind, {}).get(header) for header in ("age", "etag", "last_modified")} for kind in {**previous.get("http_metadata", {}), **current.get("http_metadata", {})}}
            previous = current
        return result

    def run(self) -> dict[str, Any]:
        started = self.clock.now()
        try:
            bootstrap = self._fetch(self.config.entry_url)
            identity, post = resolve_bootstrap_response(self.config.entry_url, bootstrap)
        except Exception as exc:
            base = self.config.output_root / self.clock.now().date().isoformat(); run_dir = base / "unresolved_live_freshness.run"; output_path = base / "unresolved_live_freshness.json"
            result = {"race": None, "scheduled_post_time": None, "run_started_at": iso(started), "run_finished_at": iso(self.clock.now()), "captures": {}, "comparison": {}, "overall_status": "FAILED", "warnings": [f"BOOTSTRAP_FAILED:{type(exc).__name__}:{exc}"], "process": {"background_processes_used": 0, "child_processes_started": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
            atomic_json(output_path, result); atomic_json(run_dir / "FAILED.json", {"overall_status": "FAILED", "finished_at": result["run_finished_at"], "output_json": str(output_path)})
            return result
        race_key = canonical_race_key(identity["race_date"], identity["venue"], identity["race_number"])
        base = self.config.output_root / identity["race_date"]
        output_path = base / f"{identity['venue']}_race{identity['race_number']:02d}_live_freshness.json"; run_dir = base / f"{identity['venue']}_race{identity['race_number']:02d}_live_freshness.run"
        marks = mark_times(post, self.config.marks); now = self.clock.now(); first = marks[self.config.marks[0][0]]
        result: dict[str, Any] = {"race": identity, "scheduled_post_time": iso(post), "run_started_at": iso(started), "run_finished_at": None, "captures": {}, "comparison": {}, "overall_status": "RUNNING", "warnings": ["T-15 is ENGINEERING_CANDIDATE and NOT_FROZEN.", "This operational JSON is not a model analysis bundle."], "process": {"background_processes_used": 0, "child_processes_started": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
        atomic_json(run_dir / "RUNNING.json", {"started_at": result["run_started_at"], "race": identity})
        try:
            if (first - now).total_seconds() > self.config.max_initial_wait_seconds:
                result["overall_status"] = "TOO_EARLY_TO_START"; result["warnings"].append("First mark exceeds bounded initial wait.")
                return result
            self._say(f"P2 LIVE FRESHNESS PROBE\nRace: {identity['venue']} {identity['race_number']}R\nPost: {identity['scheduled_post_time_local']} JST")
            for name, _, role in self.config.marks:
                checkpoint = run_dir / f"{name}.complete.json"; current = self.clock.now(); target = marks[name]
                if checkpoint.exists():
                    loaded = json.loads(checkpoint.read_text(encoding="utf-8")); loaded["status"] = "RESUMED_SUCCESS_NO_RECAPTURE"; loaded["mark"] = name; result["captures"][name] = loaded; continue
                if current > target:
                    result["captures"][name] = {"mark": name, "attempted": False, "status": "MISSED_BEFORE_START", "scheduled_at": iso(target), "errors": [], "engineering_status": "LIVE_FRESHNESS_TEST"}; continue
                wait_seconds = (target - current).total_seconds()
                if wait_seconds > 0:
                    self._say(f"Waiting for {name}..."); self.clock.sleep(wait_seconds)
                capture = self._attempt_capture(name, role, identity, post); capture["mark"] = name; capture["scheduled_at"] = iso(target)
                result["captures"][name] = capture
                if capture["status"] in {"PASS", "PARTIAL"}:
                    atomic_json(checkpoint, capture)
                self._say(f"[{name}] {capture['status']}")
            result["comparison"] = self._comparison(result["captures"])
            for capture in result["captures"].values():
                capture.pop("quotes", None)
            statuses = [value["status"] for value in result["captures"].values()]
            result["overall_status"] = "COMPLETE" if all(status in {"PASS", "RESUMED_SUCCESS_NO_RECAPTURE", "MISSED_BEFORE_START"} for status in statuses) else "COMPLETE_WITH_FAILURES"
        except Exception as exc:
            result["overall_status"] = "FAILED"; result["warnings"].append(f"FATAL:{type(exc).__name__}:{exc}"); result["traceback"] = traceback.format_exc()
        finally:
            result["run_finished_at"] = iso(self.clock.now()); atomic_json(output_path, result); (run_dir / "RUNNING.json").unlink(missing_ok=True)
            marker = "COMPLETE.json" if result["overall_status"].startswith("COMPLETE") else "FAILED.json" if result["overall_status"] == "FAILED" else "STOPPED.json"
            atomic_json(run_dir / marker, {"overall_status": result["overall_status"], "finished_at": result["run_finished_at"], "output_json": str(output_path)})
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Foreground Nankan live freshness probe; no model inference is performed.")
    parser.add_argument("race_entry_url")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--max-initial-wait-minutes", type=int, default=45)
    args = parser.parse_args()
    probe = LiveFreshnessProbe(ProbeConfig(args.race_entry_url, request_timeout_seconds=args.timeout_seconds, max_initial_wait_seconds=args.max_initial_wait_minutes * 60))
    output = probe.run(); print(f"LIVE FRESHNESS PROBE {output['overall_status']}")
    if output["race"]:
        path = OUTPUT_ROOT / output["race"]["race_date"] / f"{output['race']['venue']}_race{output['race']['race_number']:02d}_live_freshness.json"; print(f"Report: {path}")


if __name__ == "__main__":
    main()
