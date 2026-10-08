"""Add Stranger Things seasons 2-5 from TMDB to the existing CSV dataset."""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "movies.csv"
BACKUP_PATH = ROOT / "data" / "movies_backup_before_stranger_things_seasons.csv"
ENV_PATH = ROOT / ".env"
API_BASE = "https://api.themoviedb.org/3"
SERIES_TMDB_ID = 66732
SEASONS = (2, 3, 4, 5)
API_FIELDS = "season_number|name|air_date|vote_average|overview|poster_path|media_type"


def read_credentials() -> tuple[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return (
        values.get("TMDB_ACCESS_TOKEN") or os.environ.get("TMDB_ACCESS_TOKEN", ""),
        values.get("TMDB_API_KEY") or os.environ.get("TMDB_API_KEY", ""),
    )


def request_json(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(4):
        try:
            response = session.get(url, params=params, timeout=25)
            if response.status_code == 429 and attempt < 3:
                time.sleep(max(1, float(response.headers.get("Retry-After", "2"))))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("TMDB returned an unexpected response.")
            return payload
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("TMDB retries were exhausted.")


def fetch_seasons() -> list[dict[str, Any]]:
    token, api_key = read_credentials()
    if not token and not api_key:
        raise SystemExit("TMDB_ACCESS_TOKEN or TMDB_API_KEY is missing; no changes made.")
    headers = {"accept": "application/json", "User-Agent": "PopcornPicksCollegeProject/1.0"}
    params: dict[str, Any] = {"language": "en-US"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        params["api_key"] = api_key
    results = []
    with requests.Session() as session:
        session.headers.update(headers)
        for season_number in SEASONS:
            payload = request_json(session, f"{API_BASE}/tv/{SERIES_TMDB_ID}/season/{season_number}", params)
            if int(payload.get("season_number", -1)) != season_number or not payload.get("id"):
                raise SystemExit(f"TMDB season {season_number} did not validate; no changes made.")
            results.append(payload)
    return results


def main() -> None:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        original_columns = list(reader.fieldnames or [])
        rows = list(reader)
    if not original_columns or "tmdb_id" not in original_columns or "media_type" not in original_columns:
        raise SystemExit("The dataset is missing required columns; no changes made.")
    show = next((row for row in rows if row.get("media_type") == "tv" and row.get("tmdb_id") == str(SERIES_TMDB_ID)), None)
    if not show:
        raise SystemExit("The Stranger Things TV-series row was not found; no changes made.")

    columns = list(original_columns)
    for column in ("series_tmdb_id", "season_number"):
        if column not in columns:
            columns.append(column)
    existing_tmdb_ids = {row.get("tmdb_id", "") for row in rows}
    if len(existing_tmdb_ids) != len(rows):
        raise SystemExit("Existing TMDB IDs are not unique; no changes made.")

    seasons = fetch_seasons()
    additions: list[dict[str, str]] = []
    for payload in seasons:
        season_number = int(payload["season_number"])
        tmdb_id = str(payload["id"])
        title = f"Stranger Things — Season {season_number}"
        if tmdb_id in existing_tmdb_ids or any(row.get("title") == title for row in rows):
            raise SystemExit(f"A conflicting row already exists for Season {season_number}; no changes made.")
        air_date = str(payload.get("air_date") or "")
        values: dict[str, Any] = {
            "id": "",
            "tmdb_id": tmdb_id,
            "title": title,
            "year": air_date[:4] if air_date[:4].isdigit() else "",
            "rating": payload.get("vote_average", ""),
            "votes": "",
            "genres": show.get("genres", ""),
            "language": show.get("language", ""),
            "runtime": "",
            "certificate": show.get("certificate", ""),
            "director": show.get("director", ""),
            "cast": show.get("cast", ""),
            "description": payload.get("overview") or "",
            "tmdb_url": f"https://www.themoviedb.org/tv/{SERIES_TMDB_ID}/season/{season_number}",
            "api_fields": API_FIELDS,
            "selenium_fields": "",
            "poster_path": payload.get("poster_path") or "",
            "media_type": "tv_season",
            "series_tmdb_id": str(SERIES_TMDB_ID),
            "season_number": str(season_number),
        }
        additions.append({column: "" if values.get(column) is None else str(values.get(column, "")) for column in columns})

    next_id = max(int(row["id"]) for row in rows) + 1
    for offset, row in enumerate(additions):
        row["id"] = str(next_id + offset)
    final_rows = [{column: row.get(column, "") for column in columns} for row in rows] + additions

    if len({row["tmdb_id"] for row in final_rows}) != len(final_rows):
        raise SystemExit("Validation failed: duplicate TMDB IDs; no changes made.")
    if [row.get(column, "") for row in final_rows[:len(rows)] for column in original_columns] != [row.get(column, "") for row in rows for column in original_columns]:
        raise SystemExit("Validation failed: an existing dataset field changed; no changes made.")
    if {int(row["season_number"]) for row in additions} != set(SEASONS) or any(row["media_type"] != "tv_season" for row in additions):
        raise SystemExit("Validation failed: the four season records are incomplete; no changes made.")

    if not BACKUP_PATH.exists():
        shutil.copy2(CSV_PATH, BACKUP_PATH)
    fd, temp_name = tempfile.mkstemp(prefix="movies.stranger-things-seasons.", suffix=".csv.tmp", dir=CSV_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(final_rows)
            target.flush()
            os.fsync(target.fileno())
        with open(temp_name, "r", encoding="utf-8", newline="") as check:
            verified = list(csv.DictReader(check))
        if len(verified) != len(final_rows) or len({row["tmdb_id"] for row in verified}) != len(verified):
            raise RuntimeError("Temporary CSV verification failed; original was not replaced.")
        os.replace(temp_name, CSV_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    print(f"Added {len(additions)} Stranger Things seasons; dataset now has {len(final_rows)} rows.")
    for row in additions:
        print(f"{row['title']}: TMDB season {row['tmdb_id']}, poster {'available' if row['poster_path'] else 'unavailable'}")
    print(f"Backup saved to {BACKUP_PATH}")


if __name__ == "__main__":
    main()
