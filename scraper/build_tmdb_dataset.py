"""Build the first production-sized TMDB dataset without touching Flask or SQLite."""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from requests import Session
from requests.exceptions import RequestException
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from collect_tmdb import (
    BASE_URL,
    COLUMNS,
    DEFAULT_DELAY,
    DEFAULT_TIMEOUT,
    as_text,
    clean_and_validate,
    movie_from_detail,
    read_env,
)

LOGGER = logging.getLogger(__name__)
MAX_MOVIES = 2500
TARGET_MOVIES = 2000
SELENIUM_COLUMNS = ["title", "year", "rating", "runtime", "overview", "tmdb_url", "page_title"]
PROVENANCE_COLUMNS = ["api_fields", "selenium_fields"]
OUTPUT_COLUMNS = COLUMNS + PROVENANCE_COLUMNS


def discover_movies(session: Session, target: int, timeout: int, delay: float, request_count: list[int], errors: list[str]) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    seen: set[int] = set()
    query_plan: list[dict[str, Any]] = [{"sort_by": "popularity.desc"}]
    query_plan.extend(
        {"primary_release_date.gte": f"{year}-01-01", "primary_release_date.lte": f"{year}-12-31", "sort_by": "popularity.desc"}
        for year in range(2020, 2027)
    )
    # Genre-based queries with retry for the genre list request
    for attempt in range(3):
        try:
            genre_response = session.get(f"{BASE_URL}/genre/movie/list", params={"language": "en-US"}, timeout=timeout)
            request_count[0] += 1
            if genre_response.status_code == 429:
                errors.append("genre discovery: HTTP 429 rate limit")
            else:
                genre_response.raise_for_status()
                query_plan.extend(
                    {"with_genres": genre["id"], "sort_by": "popularity.desc"}
                    for genre in genre_response.json().get("genres", [])
                )
            break
        except RequestException as exc:
            LOGGER.warning("Genre discovery attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(5)
            else:
                errors.append(f"genre discovery: {type(exc).__name__}: {exc}")
    query_plan.extend(
        {"with_original_language": language, "sort_by": "popularity.desc"}
        for language in ("en", "ja", "ko", "es", "fr", "zh", "hi", "ta", "te", "de")
    )
    for query in query_plan:
        for page in range(1, 11):
            if len(seen) >= target:
                return discovered
            params = {"language": "en-US", "include_adult": "false", "page": page, **query}
            last_exc: RequestException | None = None
            for attempt in range(3):
                try:
                    request_count[0] += 1
                    response = session.get(f"{BASE_URL}/discover/movie", params=params, timeout=timeout)
                    if response.status_code == 429:
                        errors.append(f"discover page {page}: HTTP 429 rate limit")
                        return discovered
                    response.raise_for_status()
                    page_results = response.json().get("results", [])
                    if not page_results:
                        last_exc = None
                        break
                    for item in page_results:
                        tmdb_id = item.get("id")
                        if isinstance(tmdb_id, int) and tmdb_id not in seen:
                            seen.add(tmdb_id)
                            discovered.append(item)
                    last_exc = None
                    break
                except RequestException as exc:
                    last_exc = exc
                    LOGGER.warning("Discover page %d attempt %d failed: %s", page, attempt + 1, exc)
                    if attempt < 2:
                        time.sleep(5)
            if last_exc is not None:
                errors.append(f"discover page {page}: {type(last_exc).__name__}: {last_exc}")
                LOGGER.warning("Skipping query after 3 failed attempts: %s", errors[-1])
                break  # skip remaining pages for this query, continue to next query
            if delay > 0:
                time.sleep(delay)
    return discovered


def collect_api_details(
    session: Session,
    discovered: list[dict[str, Any]],
    existing_ids: set[int],
    target_new: int,
    timeout: int,
    delay: float,
    request_count: list[int],
    successful_requests: list[int],
    errors: list[str],
    existing_records: list[dict[str, Any]] | None = None,
    output_path: Path | None = None,
    checkpoint_every: int = 50,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[int] = set(existing_ids)
    consecutive_failures = 0
    for index, item in enumerate(discovered):
        tmdb_id = item.get("id")
        if not isinstance(tmdb_id, int) or tmdb_id in seen:
            continue
        seen.add(tmdb_id)
        try:
            request_count[0] += 1
            response = session.get(
                f"{BASE_URL}/movie/{tmdb_id}",
                params={"language": "en-US", "append_to_response": "credits,release_dates"},
                timeout=timeout,
            )
            if response.status_code == 429:
                errors.append(f"movie {tmdb_id}: HTTP 429 rate limit")
                break
            response.raise_for_status()
            successful_requests[0] += 1
            records.append(movie_from_detail(response.json()))
            consecutive_failures = 0
        except RequestException as exc:
            consecutive_failures += 1
            errors.append(f"movie {tmdb_id}: {type(exc).__name__}: {exc}")
            LOGGER.warning("TMDB detail failed (%d consecutive): %s", consecutive_failures, errors[-1])
            if consecutive_failures >= 5:
                errors.append("API collection stopped after 5 consecutive detail failures")
                break
        if len(records) >= target_new:
            break
        # Periodic API checkpoint: save every `checkpoint_every` new records so progress survives interruption
        if output_path is not None and len(records) % checkpoint_every == 0 and len(records) > 0:
            all_so_far = (existing_records or []) + records
            save_dataset(all_so_far, {}, output_path)
            print(f"[API checkpoint] {len(records)} new records collected ({len(all_so_far)} total) — saved to {output_path}", flush=True)
        elif len(records) % 10 == 0 and len(records) > 0:
            print(f"[API progress] {len(records)}/{target_new} new records collected", flush=True)
        if index < len(discovered) - 1 and delay > 0:
            time.sleep(delay)
    return records


def visible_text(driver: webdriver.Edge, selectors: list[str]) -> str:
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.is_displayed():
                value = as_text(element.text)
                if value:
                    return value
    return ""


def selenium_record(driver: webdriver.Edge, tmdb_id: int, url: str) -> dict[str, Any]:
    metadata = visible_text(driver, [".facts", "[data-testid='movie-facts']", ".header_info"])
    year_match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2}|21\d{2})\b", metadata)
    runtime_match = re.search(r"\b(\d+)h\s*(\d+)?m?\b", metadata, re.I)
    runtime = ""
    if runtime_match:
        runtime = int(runtime_match.group(1)) * 60 + int(runtime_match.group(2) or 0)
    return {
        "tmdb_id": tmdb_id,
        "title": visible_text(driver, ["h2", "h1", "[data-testid='movie-title']"]),
        "year": year_match.group(1) if year_match else "",
        "rating": visible_text(driver, [".user_score", "[data-testid='score']", ".rating"]),
        "runtime": runtime,
        "overview": visible_text(driver, [".overview p", "[data-testid='movie-overview']", "section p"]),
        "tmdb_url": url,
        "page_title": as_text(driver.title),
    }


def collect_selenium(
    records: list[dict[str, Any]],
    timeout: int,
    delay: float,
    errors: list[str],
    pages_visited: list[int],
    successful_pages: list[int],
    failed_pages: list[int],
    output_path: Path,
) -> dict[int, dict[str, Any]]:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1365,900")
    options.add_argument("--disable-gpu")
    driver = None
    scraped: dict[int, dict[str, Any]] = {}
    consecutive_failures = 0
    try:
        driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(timeout)
        for index, record in enumerate(records):
            tmdb_id = int(record["tmdb_id"])
            url = f"https://www.themoviedb.org/movie/{tmdb_id}"
            try:
                driver.get(url)
                WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((By.TAG_NAME, "body")))
                WebDriverWait(driver, timeout).until(
                    lambda current: as_text(current.find_element(By.TAG_NAME, "body").text) != ""
                )
                pages_visited[0] += 1
                result = selenium_record(driver, tmdb_id, url)
                if result["title"] or result["overview"] or result["page_title"]:
                    scraped[tmdb_id] = result
                    successful_pages[0] += 1
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    failed_pages[0] += 1
                    errors.append(f"movie {tmdb_id}: no visible fields extracted")
            except (TimeoutException, WebDriverException) as exc:
                consecutive_failures += 1
                failed_pages[0] += 1
                errors.append(f"movie {tmdb_id}: {type(exc).__name__}: {exc}")
                LOGGER.warning("Selenium failed (%d consecutive): %s", consecutive_failures, errors[-1])
                if consecutive_failures >= 5:
                    errors.append("Selenium collection stopped after 5 consecutive failures")
                    break
            if index < len(records) - 1 and delay > 0:
                time.sleep(delay)
            if (index + 1) % 25 == 0:
                save_dataset(records, scraped, output_path)
                print(f"[Selenium checkpoint] {index + 1}/{len(records)} pages visited ({successful_pages[0]} successful)", flush=True)
    finally:
        if driver is not None:
            driver.quit()
    return scraped


def load_existing_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path, keep_default_na=False)
    records = frame.to_dict(orient="records")
    for record in records:
        for field in ("genres", "cast"):
            record[field] = record.get(field, "")
    return records


def save_dataset(api_records: list[dict[str, Any]], selenium_records: dict[int, dict[str, Any]], output_path: Path) -> pd.DataFrame:
    merged: list[dict[str, Any]] = []
    for api_record in api_records:
        record = dict(api_record)
        tmdb_id = int(record["tmdb_id"])
        browser_record = selenium_records.get(tmdb_id, {})
        api_fields: list[str] = []
        selenium_fields: list[str] = []
        for field in COLUMNS:
            if field in {"id", "tmdb_id", "tmdb_url"}:
                continue
            if record.get(field) not in (None, "", []):
                api_fields.append(field)
        for field in SELENIUM_COLUMNS:
            value = browser_record.get(field, "")
            if value not in (None, "", []):
                selenium_fields.append(field)
                if field in {"title", "year", "rating", "runtime", "overview", "tmdb_url"} and not record.get(field):
                    record[field] = value
        record["api_fields"] = "|".join(api_fields)
        record["selenium_fields"] = "|".join(selenium_fields)
        merged.append(record)
    frame, _ = clean_and_validate(merged)
    for column in PROVENANCE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame["api_fields"] = frame["tmdb_id"].map(lambda value: next((item["api_fields"] for item in merged if item.get("tmdb_id") == value), ""))
    frame["selenium_fields"] = frame["tmdb_id"].map(lambda value: next((item["selenium_fields"] for item in merged if item.get("tmdb_id") == value), ""))
    frame.to_csv(output_path, index=False, na_rep="")
    return frame


def write_report(path: Path, values: dict[str, Any]) -> None:
    lines = ["Popcorn Picks TMDB dataset report", "=" * 36]
    for key, value in values.items():
        lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the 2,000-movie TMDB dataset.")
    parser.add_argument("--limit", type=int, default=TARGET_MOVIES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--api-delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--selenium-delay", type=float, default=0.25)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    limit = min(max(args.limit, TARGET_MOVIES), MAX_MOVIES)
    started = datetime.now(timezone.utc)
    # Resolve paths relative to the project root (parent of the scraper directory)
    _project_root = Path(__file__).resolve().parent.parent
    output_path = _project_root / "data" / "movies.csv"
    report_path = _project_root / "data" / "dataset_report.txt"
    token = read_env(_project_root / ".env").get("TMDB_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("TMDB_ACCESS_TOKEN is missing from .env")
    request_count = [0]
    successful_requests = [0]
    pages_visited = [0]
    successful_pages = [0]
    failed_pages = [0]
    api_errors: list[str] = []
    selenium_errors: list[str] = []
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json", "User-Agent": "PopcornPicksCollegeProject/1.0"}
    with requests.Session() as session:
        session.headers.update(headers)
        existing_records = load_existing_records(output_path)
        existing_ids = {int(record["tmdb_id"]) for record in existing_records if str(record.get("tmdb_id", "")).isdigit()}
        needed = max(limit - len(existing_ids), 0)
        print(f"[INFO] Existing movies: {len(existing_ids)}. Need {needed} more to reach {limit}.", flush=True)
        discovered = discover_movies(session, len(existing_ids) + needed + 300, args.timeout, args.api_delay, request_count, api_errors)
        print(f"[INFO] Discovered {len(discovered)} candidate movies from TMDB.", flush=True)
        new_api_records = collect_api_details(
            session, discovered, existing_ids, needed, args.timeout, args.api_delay,
            request_count, successful_requests, api_errors,
            existing_records=existing_records, output_path=output_path, checkpoint_every=50,
        )
        print(f"[INFO] API collection done: {len(new_api_records)} new records collected.", flush=True)
    api_records = existing_records + new_api_records
    new_records = [record for record in new_api_records if int(record["tmdb_id"]) not in {int(item["tmdb_id"]) for item in existing_records}]
    existing_selenium = {
        int(record["tmdb_id"]): {"tmdb_id": int(record["tmdb_id"]), "title": record.get("title", ""), "year": record.get("year", ""), "rating": record.get("rating", ""), "runtime": record.get("runtime", ""), "overview": record.get("description", ""), "tmdb_url": record.get("tmdb_url", ""), "page_title": ""}
        for record in existing_records
        if "selenium" in str(record.get("selenium_fields", ""))
    }
    print(f"[INFO] Starting Selenium for {len(new_records)} new records ({len(existing_selenium)} existing selenium records retained).", flush=True)
    selenium_records = collect_selenium(new_records, args.timeout, args.selenium_delay, selenium_errors, pages_visited, successful_pages, failed_pages, output_path)
    selenium_records.update(existing_selenium)
    frame = save_dataset(api_records, selenium_records, output_path)
    missing = {column: int(frame[column].fillna("").astype(str).eq("").sum()) for column in frame.columns}
    raw_ids = [record.get("tmdb_id") for record in api_records]
    duplicates = len(raw_ids) - len(set(raw_ids))
    report_values = {
        "collection_timestamp_utc": started.isoformat(),
        "api_requests_made": request_count[0],
        "api_successful_requests": successful_requests[0],
        "api_failed_requests": len(api_errors),
        "selenium_pages_visited": pages_visited[0] + len(existing_selenium),
        "selenium_successful_pages": successful_pages[0] + len(existing_selenium),
        "selenium_failed_pages": failed_pages[0],
        "movies_discovered": len(discovered),
        "movies_successfully_collected": len(api_records),
        "movies_after_cleaning": len(frame),
        "duplicates_removed": duplicates,
        "missing_value_counts": missing,
        "fields_available": list(frame.columns),
        "api_errors": api_errors or "none",
        "selenium_errors": selenium_errors or "none",
        "csv_bytes": output_path.stat().st_size,
    }
    write_report(report_path, report_values)
    print(report_values)
    print(frame.head(3).fillna("").to_json(orient="records"))


if __name__ == "__main__":
    main()
