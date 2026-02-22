#!/usr/bin/env python3
"""Poll a Sodiw rankings page and post updates every few minutes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_URL = "https://www.sodiwseries.com/en-gb/rankings/global/2026/slovakia-c37/junior-cup-3"


@dataclass(frozen=True)
class RankingEntry:
    position: str
    name: str
    points: str


class RankingTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table" and not self.in_table:
            self.in_table = True
            return

        if not self.in_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.in_table and self.in_row and self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_cell:
            cell_text = " ".join(" ".join(self.current_cell).split())
            self.current_row.append(cell_text)
            self.current_cell = []
            self.in_cell = False
            return

        if tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
            self.in_row = False
            return

        if tag == "table" and self.in_table:
            self.in_table = False


class ScriptContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_script = False
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "script":
            self.in_script = True

    def handle_data(self, data: str) -> None:
        if self.in_script:
            cleaned = data.strip()
            if cleaned:
                self.scripts.append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_script = False


def fetch_html(url: str, timeout_s: int) -> str:
    request = Request(url, headers={"User-Agent": "ranking-watcher/1.0"})
    with urlopen(request, timeout=timeout_s) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_rankings_from_table(html: str) -> list[RankingEntry]:
    parser = RankingTableParser()
    parser.feed(html)

    entries: list[RankingEntry] = []
    for row in parser.rows:
        if len(row) < 3:
            continue
        entries.append(RankingEntry(position=row[0], name=row[1], points=row[2]))

    return entries


def parse_rankings_from_embedded_json(html: str) -> list[RankingEntry]:
    script_parser = ScriptContentParser()
    script_parser.feed(html)

    candidates: list[RankingEntry] = []

    for script_content in script_parser.scripts:
        for payload in extract_json_payloads(script_content):
            candidates.extend(extract_entries_from_json(payload))

    deduped: list[RankingEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in candidates:
        key = (entry.position, entry.name, entry.points)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)

    return deduped


def extract_json_payloads(script_content: str) -> list[Any]:
    payloads: list[Any] = []

    script_content = script_content.strip().rstrip(";")

    direct_candidates = [script_content]
    assign_match = re.search(r"=\s*(\{.*\}|\[.*\])\s*$", script_content, flags=re.DOTALL)
    if assign_match:
        direct_candidates.append(assign_match.group(1))

    for candidate in direct_candidates:
        parsed = try_parse_json(candidate)
        if parsed is not None:
            payloads.append(parsed)

    for match in re.finditer(r"(\{[\s\S]*\}|\[[\s\S]*\])", script_content):
        parsed = try_parse_json(match.group(1))
        if parsed is not None:
            payloads.append(parsed)

    return payloads


def try_parse_json(value: str) -> Any | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def extract_entries_from_json(payload: Any) -> list[RankingEntry]:
    results: list[RankingEntry] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            if node and all(isinstance(item, dict) for item in node):
                maybe_entries = try_convert_dict_list_to_entries(node)
                if maybe_entries:
                    results.extend(maybe_entries)
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            maybe_entries = try_convert_dict_list_to_entries(node.get("data") if "data" in node else None)
            if maybe_entries:
                results.extend(maybe_entries)
            for value in node.values():
                walk(value)

    walk(payload)
    return results


def try_convert_dict_list_to_entries(node: Any) -> list[RankingEntry] | None:
    if not isinstance(node, list) or not node:
        return None
    if not all(isinstance(item, dict) for item in node):
        return None

    entries: list[RankingEntry] = []
    for item in node:
        name = first_present(item, ["name", "driverName", "driver", "participant", "teamName", "fullName"])
        points = first_present(item, ["points", "point", "score", "totalPoints", "pts"])
        position = first_present(item, ["position", "rank", "place", "ranking"])

        if name is None or points is None:
            continue

        if position is None:
            position = str(len(entries) + 1)

        entries.append(
            RankingEntry(
                position=str(position).strip(),
                name=str(name).strip(),
                points=str(points).strip(),
            )
        )

    return entries or None


def first_present(item: dict[str, Any], keys: list[str]) -> Any | None:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def fetch_rankings(url: str, timeout_s: int, debug_html_file: str | None = None) -> list[RankingEntry]:
    try:
        html = fetch_html(url, timeout_s)
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Could not fetch rankings page: {exc}") from exc

    if debug_html_file:
        with open(debug_html_file, "w", encoding="utf-8") as f:
            f.write(html)

    entries = parse_rankings_from_table(html)
    if entries:
        return entries

    entries = parse_rankings_from_embedded_json(html)
    if entries:
        return entries

    raise RuntimeError(
        "No ranking rows were parsed. The page may now use a different structure/API. "
        "Try --always-post with --output-file and inspect raw page/source."
    )


def normalize_entries(entries: Iterable[RankingEntry]) -> str:
    return "\n".join(f"{entry.position}|{entry.name}|{entry.points}" for entry in entries)


def build_report(entries: Iterable[RankingEntry], source_url: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"Ranking snapshot ({timestamp})", f"Source: {source_url}", ""]
    for entry in entries:
        lines.append(f"{entry.position}. {entry.name} — {entry.points} pts")
    return "\n".join(lines)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def post_to_webhook(webhook_url: str, message: str, timeout_s: int) -> None:
    payload = json.dumps({"text": message}).encode("utf-8")
    request = Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ranking-watcher/1.0"},
        method="POST",
    )

    with urlopen(request, timeout=timeout_s):
        return


def post_to_stdout(message: str) -> None:
    print(message)
    print("-" * 72)


def save_report(path: str, message: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(message)
        f.write("\n")


def save_state(state_path: str, state: dict[str, str]) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def load_state(state_path: str) -> dict[str, str]:
    if not os.path.exists(state_path):
        return {}

    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Sodiw rankings periodically and post updates when changes appear."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Ranking page URL to poll.")
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Polling interval in seconds (default: 300 = 5 minutes).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--state-file",
        default=".ranking_state.json",
        help="Path to store the last posted snapshot hash.",
    )
    parser.add_argument(
        "--output-file",
        default="ranking_latest.txt",
        help="Path to always write the latest parsed ranking snapshot.",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.getenv("WEBHOOK_URL"),
        help="Webhook URL to post updates (optional, defaults to WEBHOOK_URL env var).",
    )
    parser.add_argument(
        "--always-post",
        action="store_true",
        help="Post every polling cycle, even if content has not changed.",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run a single polling cycle and exit (useful for testing).",
    )
    parser.add_argument(
        "--debug-html-file",
        default=None,
        help="Optional path to write fetched page HTML for troubleshooting parsing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.interval < 30:
        print("For safety, --interval must be at least 30 seconds.", file=sys.stderr)
        return 2

    state = load_state(args.state_file)
    last_hash = state.get("last_hash")

    print(f"Starting ranking watcher. Polling every {args.interval} seconds.", flush=True)
    print(f"Source URL: {args.url}", flush=True)
    print("Posting destination:", "webhook" if args.webhook_url else "stdout", flush=True)
    print(f"Latest ranking snapshot file: {args.output_file}", flush=True)

    while True:
        try:
            entries = fetch_rankings(args.url, timeout_s=args.timeout, debug_html_file=args.debug_html_file)
            normalized = normalize_entries(entries)
            report = build_report(entries, args.url)
            current_hash = content_hash(normalized)

            save_report(args.output_file, report)

            if args.always_post or current_hash != last_hash:
                if args.webhook_url:
                    post_to_webhook(args.webhook_url, report, timeout_s=args.timeout)
                else:
                    post_to_stdout(report)

                print(f"[{datetime.now().isoformat(timespec='seconds')}] Posted updated ranking.")
                last_hash = current_hash
                save_state(args.state_file, {"last_hash": last_hash})
            else:
                print(f"[{datetime.now().isoformat(timespec='seconds')}] No ranking changes detected.")

        except Exception as exc:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] Error: {exc}", file=sys.stderr)

        if args.run_once:
            break

        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
