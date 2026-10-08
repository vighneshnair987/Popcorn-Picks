"""Small, non-bypass Playwright diagnostic for publicly accessible IMDb pages."""

from __future__ import annotations

import argparse
import json
import logging
import re
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

LOGGER = logging.getLogger(__name__)
DEFAULT_URL = "https://www.imdb.com/chart/top/"
CSV_COLUMNS = [
    "imdb_id", "title", "year", "runtime", "genres", "rating", "votes",
    "certificate", "director", "cast", "description", "language", "imdb_url",
]
IMDB_ID_PATTERN = re.compile(r"/title/(tt\d+)")
RESTRICTION_MARKERS = ("captcha", "robot check", "access denied", "forbidden")


def empty_record() -> dict[str, Any]:
    return {column: None for column in CSV_COLUMNS}


def text(value: Any) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def as_text_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("name")
        item = text(item)
        if item and item not in result:
            result.append(item)
    return result


def parse_json_ld(html: str, url: str) -> dict[str, Any] | None:
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    ):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = value if isinstance(value, list) else [value]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("@type") not in {"Movie", "TVMovie", "MovieSeries"}:
                continue
            match = IMDB_ID_PATTERN.search(url)
            aggregate = item.get("aggregateRating") or {}
            record = empty_record()
            record.update(
                {
                    "imdb_id": match.group(1) if match else None,
                    "title": text(item.get("name")),
                    "year": text(item.get("datePublished")),
                    "runtime": text(item.get("duration")),
                    "genres": "|".join(as_text_list(item.get("genre"))),
                    "rating": text(aggregate.get("ratingValue")),
                    "votes": text(aggregate.get("ratingCount")),
                    "certificate": text(item.get("contentRating")),
                    "director": "|".join(as_text_list(item.get("director"))),
                    "cast": "|".join(as_text_list(item.get("actor"))),
                    "description": text(item.get("description")),
                    "language": text(item.get("inLanguage")),
                    "imdb_url": url,
                }
            )
            return record
    return None


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame(records, columns=CSV_COLUMNS)
    frame.to_csv(path, index=False)


def run_diagnostic(url: str, limit: int, raw_path: Path, clean_path: Path) -> int:
    report: dict[str, Any] = {
        "playwright_version": None,
        "browser": "chromium",
        "url": url,
        "status": None,
        "page_title": None,
        "usable_content": False,
        "records": 0,
        "fields": [],
        "valid_ids": False,
        "errors": [],
        "browser_closed": False,
    }
    records: list[dict[str, Any]] = []
    try:
        with sync_playwright() as playwright:
            report["playwright_version"] = version("playwright")
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 "
                        "PopcornPicksCollegeProject/1.0"
                    )
                )
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_load_state("load", timeout=30000)
                report["status"] = response.status if response else None
                report["page_title"] = page.title()
                html = page.content()
                body_text = page.locator("body").inner_text(timeout=10000).lower()
                report["usable_content"] = len(html) > 500 and bool(body_text.strip())

                restriction = any(marker in body_text for marker in RESTRICTION_MARKERS)
                if report["status"] in {401, 403, 429} or restriction or not report["usable_content"]:
                    reason = "access restriction or unusable/empty IMDb response"
                    if report["status"] in {401, 403, 429}:
                        reason = f"IMDb returned HTTP {report['status']}"
                    elif restriction:
                        reason = "IMDb page contains an access-restriction marker"
                    report["errors"].append(reason)
                else:
                    for link in page.locator('a[href*="/title/tt"]').all()[:limit]:
                        href = link.get_attribute("href")
                        if not href:
                            continue
                        title_url = "https://www.imdb.com" + href.split("?")[0]
                        title_page = page.context.new_page()
                        try:
                            title_response = title_page.goto(title_url, wait_until="domcontentloaded", timeout=30000)
                            title_page.wait_for_load_state("load", timeout=30000)
                            title_html = title_page.content()
                            title_text = title_page.locator("body").inner_text(timeout=10000).lower()
                            if title_response and title_response.status in {401, 403, 429}:
                                report["errors"].append(f"{title_url}: HTTP {title_response.status}")
                                break
                            record = parse_json_ld(title_html, title_page.url)
                            if record:
                                records.append(record)
                        except (PlaywrightError, PlaywrightTimeoutError) as exc:
                            report["errors"].append(f"{title_url}: {type(exc).__name__}: {exc}")
                        finally:
                            title_page.close()
                report["records"] = len(records)
                report["fields"] = sorted({key for record in records for key, value in record.items() if value not in (None, "")})
                report["valid_ids"] = bool(records) and all(re.fullmatch(r"tt\d+", record["imdb_id"] or "") for record in records)
            finally:
                browser.close()
                report["browser_closed"] = True
    except (PlaywrightError, PlaywrightTimeoutError, OSError) as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")

    write_csv(records, raw_path)
    write_csv(records, clean_path)
    print(json.dumps(report, indent=2))
    print(f"raw_csv={raw_path} rows={len(records)} columns={CSV_COLUMNS}")
    print(f"clean_csv={clean_path} rows={len(records)} columns={CSV_COLUMNS}")
    return 0 if not report["errors"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a maximum-three-record IMDb Playwright diagnostic.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--raw-output", default="data/playwright_raw_test.csv")
    parser.add_argument("--clean-output", default="data/playwright_clean_test.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run_diagnostic(
        args.url,
        max(1, min(args.limit, 3)),
        Path(args.raw_output),
        Path(args.clean_output),
    )


if __name__ == "__main__":
    main()
