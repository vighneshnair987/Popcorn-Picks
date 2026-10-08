"""Small TMDB collection and validation pipeline for Popcorn Picks."""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from requests import Session
from requests.exceptions import RequestException

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://api.themoviedb.org/3"
DEFAULT_LIMIT = 50
DEFAULT_TIMEOUT = 30
DEFAULT_DELAY = 0.15
TMDB_ID_PATTERN = re.compile(r"^\d+$")
COLUMNS = [
    "id", "tmdb_id", "title", "year", "rating", "votes", "genres",
    "language", "runtime", "certificate", "director", "cast", "description",
    "tmdb_url",
]
NUMERIC_COLUMNS = ["id", "tmdb_id", "year", "rating", "votes", "runtime"]
MULTI_VALUE_COLUMNS = ["genres", "cast"]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def pipe_names(items: Any, key: str = "name") -> str:
    if not isinstance(items, list):
        return ""
    names: list[str] = []
    for item in items:
        value = item.get(key) if isinstance(item, dict) else item
        value = as_text(value)
        if value and value not in names:
            names.append(value)
    return "|".join(names)


def certificate_from_release_dates(payload: dict[str, Any]) -> str:
    results = payload.get("release_dates_results", {}).get("results", [])
    preferred: list[str] = []
    for country in results:
        for release in country.get("release_dates", []):
            certification = as_text(release.get("certification"))
            if certification:
                if country.get("iso_3166_1") == "US":
                    preferred.insert(0, certification)
                else:
                    preferred.append(certification)
    return preferred[0] if preferred else ""


def movie_from_detail(payload: dict[str, Any]) -> dict[str, Any]:
    credits = payload.get("credits") or {}
    directors = [
        person.get("name")
        for person in credits.get("crew", [])
        if person.get("job") == "Director"
    ]
    release_date = as_text(payload.get("release_date"))
    return {
        "id": "",
        "tmdb_id": payload.get("id", ""),
        "title": as_text(payload.get("title")),
        "year": release_date[:4] if release_date else "",
        "rating": payload.get("vote_average", ""),
        "votes": payload.get("vote_count", ""),
        "genres": pipe_names(payload.get("genres")),
        "language": as_text(payload.get("original_language")),
        "runtime": payload.get("runtime", ""),
        "certificate": certificate_from_release_dates(payload),
        "director": pipe_names(directors),
        "cast": pipe_names((credits.get("cast") or [])[:10]),
        "description": as_text(payload.get("overview")),
        "tmdb_url": f"https://www.themoviedb.org/movie/{payload.get('id', '')}",
    }


def discover_movies(
    session: Session,
    limit: int,
    timeout: int,
    request_count: list[int],
    errors: list[str],
) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    pages = (limit + 19) // 20
    for page in range(1, pages + 1):
        try:
            request_count[0] += 1
            response = session.get(
                f"{BASE_URL}/discover/movie",
                params={
                    "language": "en-US",
                    "sort_by": "popularity.desc",
                    "include_adult": "false",
                    "page": page,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            discovered.extend(response.json().get("results", []))
        except RequestException as exc:
            errors.append(f"discover page {page}: {type(exc).__name__}: {exc}")
            LOGGER.warning("TMDB discover page %d failed: %s", page, exc)
            break
        if len(discovered) >= limit:
            break
    return discovered[:limit]


def collect_details(
    session: Session,
    discovered: list[dict[str, Any]],
    timeout: int,
    delay: float,
    request_count: list[int],
    errors: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(discovered):
        tmdb_id = item.get("id")
        if not isinstance(tmdb_id, int) or tmdb_id in seen:
            continue
        seen.add(tmdb_id)
        try:
            response = session.get(
                f"{BASE_URL}/movie/{tmdb_id}",
                params={
                    "language": "en-US",
                    "append_to_response": "credits,release_dates",
                },
                timeout=timeout,
            )
            request_count[0] += 1
            if response.status_code == 429:
                errors.append(f"movie {tmdb_id}: HTTP 429 rate limit")
                LOGGER.warning("TMDB rate limit reached at movie %s", tmdb_id)
                break
            response.raise_for_status()
            records.append(movie_from_detail(response.json()))
        except RequestException as exc:
            errors.append(f"movie {tmdb_id}: {type(exc).__name__}: {exc}")
            LOGGER.warning("TMDB detail request failed for movie %s: %s", tmdb_id, exc)
        if index < len(discovered) - 1 and delay > 0:
            import time
            time.sleep(delay)
    return records


def clean_and_validate(records: list[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.DataFrame(records, columns=COLUMNS)
    raw_count = len(frame)
    duplicate_count = int(frame.duplicated(subset=["tmdb_id"]).sum()) if raw_count else 0
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[COLUMNS].copy()
    for column in frame.columns:
        if column not in NUMERIC_COLUMNS:
            frame[column] = frame[column].map(as_text)
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in MULTI_VALUE_COLUMNS:
        frame[column] = frame[column].map(
            lambda value: "|".join(dict.fromkeys(part.strip() for part in str(value).split("|") if part.strip()))
        )
    frame = frame[frame["tmdb_id"].notna() & frame["tmdb_id"].astype("Int64").astype(str).str.match(TMDB_ID_PATTERN)]
    frame = frame[frame["title"].astype(str).str.strip().ne("")]
    frame = frame.drop_duplicates(subset=["tmdb_id"], keep="first").reset_index(drop=True)
    frame["year"] = frame["year"].where(frame["year"].between(1880, 2100))
    frame["rating"] = frame["rating"].where(frame["rating"].between(0, 10))
    frame["votes"] = frame["votes"].where(frame["votes"] >= 0)
    frame["runtime"] = frame["runtime"].where(frame["runtime"] > 0)
    frame["id"] = range(1, len(frame) + 1)
    frame = frame[COLUMNS]
    missing = {column: int(frame[column].isna().sum() + frame[column].astype(str).eq("").sum()) for column in COLUMNS}
    validation = {
        "raw_records": raw_count,
        "clean_records": len(frame),
        "duplicates_removed": duplicate_count,
        "valid_tmdb_ids": bool(frame["tmdb_id"].notna().all()) if len(frame) else False,
        "numeric_rating": bool(frame["rating"].dropna().between(0, 10).all()),
        "numeric_votes": bool(frame["votes"].dropna().ge(0).all()),
        "valid_year": bool(frame["year"].dropna().between(1880, 2100).all()),
        "valid_runtime": bool(frame["runtime"].dropna().gt(0).all()),
        "non_empty_titles": bool(frame["title"].astype(str).str.strip().ne("").all()),
        "duplicate_records": int(frame.duplicated(subset=["tmdb_id"]).sum()),
        "missing_values": missing,
    }
    return frame, validation


def run(limit: int = DEFAULT_LIMIT, timeout: int = DEFAULT_TIMEOUT, delay: float = DEFAULT_DELAY) -> None:
    token = read_env(Path(".env")).get("TMDB_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("TMDB_ACCESS_TOKEN is missing from .env")
    request_count = [0]
    errors: list[str] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
        "User-Agent": "PopcornPicksCollegeProject/1.0",
    }
    with requests.Session() as session:
        session.headers.update(headers)
        discovered = discover_movies(
            session,
            min(max(limit, 1), DEFAULT_LIMIT),
            timeout,
            request_count,
            errors,
        )
        records = collect_details(session, discovered, timeout, delay, request_count, errors)
    frame, validation = clean_and_validate(records)
    output = Path("data/movies_test.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, na_rep="")
    print({
        "api_requests": request_count[0],
        "movies_collected": len(records),
        "movies_after_cleaning": len(frame),
        "duplicates_removed": validation["duplicates_removed"],
        "csv_columns": COLUMNS,
        "missing_values": validation["missing_values"],
        "validation": validation,
        "csv_bytes": output.stat().st_size,
        "api_errors": errors,
    })
    print(frame.head(3).fillna("").to_json(orient="records"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and validate a small TMDB movie dataset.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run(args.limit, args.timeout, args.delay)


if __name__ == "__main__":
    main()
