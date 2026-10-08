"""Small Selenium-only diagnostic for public TMDB movie webpages."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

OUTPUT_COLUMNS = ["tmdb_id", "title", "year", "rating", "runtime", "overview", "tmdb_url", "page_title"]


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def first_visible_text(driver: webdriver.Edge, selectors: list[str]) -> str:
    for selector in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            if element.is_displayed():
                value = clean_text(element.text)
                if value:
                    return value
    return ""


def extract_record(driver: webdriver.Edge, tmdb_id: int, url: str) -> dict[str, str | int]:
    title = first_visible_text(driver, ["h2", "h1", "[data-testid='movie-title']"])
    overview = first_visible_text(driver, [".overview p", "[data-testid='movie-overview']", "section p"])
    metadata = first_visible_text(driver, [".facts", "[data-testid='movie-facts']", ".header_info"])
    year_match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2}|21\d{2})\b", metadata)
    runtime_match = re.search(r"\b(\d+)h\s*(\d+)?m?\b", metadata, re.I)
    runtime = ""
    if runtime_match:
        runtime = int(runtime_match.group(1)) * 60 + int(runtime_match.group(2) or 0)
    rating = first_visible_text(driver, [".user_score", "[data-testid='score']", ".rating"])
    return {
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year_match.group(1) if year_match else "",
        "rating": rating,
        "runtime": runtime,
        "overview": overview,
        "tmdb_url": url,
        "page_title": clean_text(driver.title),
    }


def run(input_path: Path, output_path: Path, limit: int = 3, timeout: int = 30) -> None:
    source = pd.read_csv(input_path).head(max(1, min(limit, 3)))
    ids = [int(value) for value in source["tmdb_id"].dropna().tolist()]
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1365,900")
    options.add_argument("--disable-gpu")
    driver = None
    records: list[dict[str, str | int]] = []
    errors: list[str] = []
    loaded_pages = 0
    urls: list[str] = []
    try:
        driver = webdriver.Edge(options=options)
        for tmdb_id in ids:
            url = f"https://www.themoviedb.org/movie/{tmdb_id}"
            urls.append(url)
            try:
                driver.get(url)
                WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((By.TAG_NAME, "body"))
                )
                WebDriverWait(driver, timeout).until(
                    lambda current: clean_text(current.find_element(By.TAG_NAME, "body").text) != ""
                )
                loaded_pages += 1
                record = extract_record(driver, tmdb_id, url)
                if record["title"] or record["overview"] or record["page_title"]:
                    records.append(record)
                else:
                    errors.append(f"{url}: no usable visible movie content")
            except (TimeoutException, WebDriverException) as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
    finally:
        if driver is not None:
            driver.quit()

    frame = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    frame.to_csv(output_path, index=False, na_rep="")
    missing = {
        column: int(frame[column].fillna("").astype(str).eq("").sum())
        for column in OUTPUT_COLUMNS
    }
    print({
        "selenium_version": __import__("selenium").__version__,
        "browser": "MicrosoftEdge",
        "browser_version": driver.capabilities.get("browserVersion") if driver else "unknown",
        "urls_tested": urls,
        "pages_loaded": loaded_pages,
        "records_extracted": len(frame),
        "fields_extracted": [column for column in OUTPUT_COLUMNS if len(frame) and frame[column].fillna("").astype(str).str.strip().ne("").any()],
        "missing_fields": missing,
        "errors": errors,
        "browser_closed": True,
        "csv": str(output_path),
        "csv_bytes": output_path.stat().st_size,
    })
    if len(frame):
        print(frame.head(1).fillna("").to_json(orient="records"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Selenium extraction from up to three TMDB pages.")
    parser.add_argument("--input", default="data/movies_test.csv")
    parser.add_argument("--output", default="data/tmdb_selenium_test.csv")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    run(Path(args.input), Path(args.output), args.limit, args.timeout)


if __name__ == "__main__":
    main()
