"""Collect movie metadata from IMDb with Requests and Selenium.

This module intentionally performs collection only. It does not write to the
existing database and does not connect to the Flask application.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests import Session
from requests.exceptions import RequestException

try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:  # Keep module importable for collection-only environments.
    webdriver = None
    WebDriverException = RuntimeError
    Options = None
    By = None
    EC = None
    WebDriverWait = None

LOGGER = logging.getLogger(__name__)
DEFAULT_DISCOVERY_URL = "https://www.imdb.com/chart/top/"
DEFAULT_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 "
    "PopcornPicksCollegeProject/1.0"
)
MOVIE_FIELDS = [
    "imdb_id",
    "title",
    "year",
    "runtime",
    "genres",
    "rating",
    "votes",
    "certificate",
    "director",
    "cast",
    "description",
    "language",
    "imdb_url",
]
IMDB_ID_PATTERN = re.compile(r"/title/(tt\d+)")


def empty_record() -> dict[str, Any]:
    return {field: None for field in MOVIE_FIELDS}


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def first_value(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def parse_number(value: Any, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    number = float(match.group())
    return int(number) if integer else number


def parse_year(value: Any) -> int | None:
    number = parse_number(value, integer=True)
    return number if number and 1800 <= number <= 2100 else None


def parse_runtime(value: Any) -> int | None:
    if value is None:
        return None
    iso_match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", str(value).strip(), re.I)
    if iso_match:
        return int(iso_match.group(1) or 0) * 60 + int(iso_match.group(2) or 0)
    return parse_number(value, integer=True)


def as_names(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("name")
        item = normalize_text(item)
        if item and item not in names:
            names.append(item)
    return names


def extract_imdb_id(url: str) -> str | None:
    match = IMDB_ID_PATTERN.search(url)
    return match.group(1) if match else None


def json_ld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict):
                if "@graph" in candidate and isinstance(candidate["@graph"], list):
                    objects.extend(item for item in candidate["@graph"] if isinstance(item, dict))
                else:
                    objects.append(candidate)
    return objects


def _meta(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.select_one(f'meta[property="{key}"], meta[name="{key}"]')
    return normalize_text(tag.get("content")) if tag else None


def parse_movie_html(html: str, url: str) -> dict[str, Any]:
    """Extract best-effort movie fields from an IMDb HTML document."""
    soup = BeautifulSoup(html, "html.parser")
    record = empty_record()
    record["imdb_id"] = extract_imdb_id(url)
    record["imdb_url"] = url

    movie_data = next(
        (
            item
            for item in json_ld_objects(soup)
            if item.get("@type") in {"Movie", "TVMovie", "MovieSeries"}
        ),
        {},
    )
    aggregate = movie_data.get("aggregateRating") or {}
    record.update(
        {
            "title": normalize_text(movie_data.get("name")) or _meta(soup, "og:title"),
            "year": parse_year(movie_data.get("datePublished")),
            "runtime": parse_runtime(movie_data.get("duration")),
            "genres": as_names(movie_data.get("genre")),
            "rating": parse_number(aggregate.get("ratingValue")),
            "votes": parse_number(aggregate.get("ratingCount"), integer=True),
            "certificate": normalize_text(movie_data.get("contentRating")),
            "director": as_names(movie_data.get("director")),
            "cast": as_names(movie_data.get("actor")),
            "description": normalize_text(movie_data.get("description")) or _meta(soup, "og:description"),
            "language": normalize_text(first_value(movie_data.get("inLanguage"))),
        }
    )

    if record["year"] is None:
        record["year"] = parse_year(_meta(soup, "release_date"))
    if not record["genres"]:
        record["genres"] = as_names(_meta(soup, "genre"))
    return record


def request_html(session: Session, url: str, timeout: int = DEFAULT_TIMEOUT) -> str | None:
    """Retrieve one IMDb page, returning None for an isolated failure."""
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.text
    except RequestException as exc:
        LOGGER.warning("Requests failed for %s: %s", url, exc)
        return None


def discover_imdb_urls(
    session: Session,
    discovery_url: str = DEFAULT_DISCOVERY_URL,
    limit: int = 3,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """Discover title URLs from an IMDb page instead of hardcoding movie titles."""
    html = request_html(session, discovery_url, timeout)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select('a[href*="/title/tt"]'):
        href = link.get("href")
        if not href:
            continue
        movie_id = extract_imdb_id(href)
        if movie_id and movie_id not in {extract_imdb_id(item) for item in urls}:
            urls.append(urljoin("https://www.imdb.com", href.split("?")[0]))
        if len(urls) >= limit:
            break
    if not urls:
        LOGGER.warning("No IMDb title URLs were discovered from %s", discovery_url)
    return urls


def collect_with_requests(
    urls: Iterable[str], session: Session | None = None, timeout: int = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Collect and parse movie pages through normal HTTP requests."""
    active_session = session or requests.Session()
    records: list[dict[str, Any]] = []
    for url in urls:
        html = request_html(active_session, url, timeout)
        if html:
            records.append(parse_movie_html(html, url))
    return records


def collect_with_selenium(
    urls: Iterable[str], timeout: int = DEFAULT_TIMEOUT, headless: bool = True
) -> list[dict[str, Any]]:
    """Collect movie pages through a normal Selenium-managed browser."""
    if webdriver is None:
        LOGGER.error("Selenium is not installed; browser collection was skipped.")
        return []

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1365,900")
    options.add_argument("--disable-gpu")
    driver = None
    records: list[dict[str, Any]] = []
    try:
        driver = webdriver.Chrome(options=options)
        for url in urls:
            try:
                driver.get(url)
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                records.append(parse_movie_html(driver.page_source, driver.current_url))
            except (WebDriverException, TimeoutError) as exc:
                LOGGER.warning("Selenium failed for %s: %s", url, exc)
    except WebDriverException as exc:
        LOGGER.error("Selenium browser initialization failed: %s", exc)
    finally:
        if driver is not None:
            driver.quit()
    return records


def combine_records(*record_groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge partial records by IMDb ID, preferring non-empty values."""
    combined: dict[str, dict[str, Any]] = {}
    for group in record_groups:
        for incoming in group:
            imdb_id = incoming.get("imdb_id")
            if not imdb_id:
                continue
            current = combined.setdefault(imdb_id, empty_record())
            for field in MOVIE_FIELDS:
                value = incoming.get(field)
                if value not in (None, "", []):
                    current[field] = value
    return list(combined.values())


def csv_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return "|".join(str(item) for item in value if item)
    return str(value)


def write_csv(records: Iterable[dict[str, Any]], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MOVIE_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: csv_value(record.get(field)) for field in MOVIE_FIELDS})


def collect_movies(
    urls: list[str] | None = None,
    limit: int = 3,
    discovery_url: str = DEFAULT_DISCOVERY_URL,
    timeout: int = DEFAULT_TIMEOUT,
    use_selenium: bool = True,
) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    selected_urls = urls or discover_imdb_urls(session, discovery_url, limit, timeout)
    selected_urls = selected_urls[:limit]
    if not selected_urls:
        LOGGER.warning("No IMDb URLs available for collection.")
        return []
    request_records = collect_with_requests(selected_urls, session, timeout)
    selenium_records = collect_with_selenium(selected_urls, timeout) if use_selenium else []
    return combine_records(request_records, selenium_records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect a small sample of IMDb movie data.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--url", action="append", dest="urls", help="IMDb title URL; repeat for multiple titles")
    parser.add_argument("--discovery-url", default=DEFAULT_DISCOVERY_URL)
    parser.add_argument("--output", default="data/raw_test.csv")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--no-selenium", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    records = collect_movies(
        urls=args.urls,
        limit=max(1, min(args.limit, 10)),
        discovery_url=args.discovery_url,
        timeout=args.timeout,
        use_selenium=not args.no_selenium,
    )
    write_csv(records, args.output)
    LOGGER.info("Collected %d records into %s", len(records), args.output)


if __name__ == "__main__":
    main()
