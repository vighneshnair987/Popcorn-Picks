"""Add the main TMDB TV-series entries requested for the Popcorn Picks demo."""

from __future__ import annotations

import csv
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "movies.csv"
BACKUP_PATH = ROOT / "data" / "movies_backup_before_tv_additions.csv"
ENV_PATH = ROOT / ".env"
API_BASE = "https://api.themoviedb.org/3"
FEATURED_SERIES = ("Stranger Things", "IT: Welcome to Derry")
SERIES_API_FIELDS = "name|first_air_date|vote_average|vote_count|genres|original_language|episode_run_time|created_by|aggregate_credits|overview|poster_path|media_type"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def request_json(session: requests.Session, url: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    last_error: requests.RequestException | None = None
    for attempt in range(4):
        try:
            response = session.get(url, params=params, timeout=timeout)
        except requests.RequestException as error:
            last_error = error
            if attempt < 3:
                time.sleep(2**attempt)
                continue
            break
        if response.status_code == 429 and attempt < 3:
            delay = response.headers.get("Retry-After", "2")
            try:
                time.sleep(max(1.0, float(delay)))
            except ValueError:
                time.sleep(2.0)
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("TMDB returned an unexpected response.")
        return payload
    if last_error:
        raise RuntimeError("TMDB request failed after bounded connection retries.") from last_error
    raise RuntimeError("TMDB rate limit retries were exhausted.")


def normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def content_rating(payload: dict[str, Any]) -> str:
    ratings = (payload.get("content_ratings") or {}).get("results", [])
    for country_code in ("IN", "US"):
        for item in ratings:
            if isinstance(item, dict) and item.get("iso_3166_1") == country_code and item.get("rating"):
                return str(item["rating"]).strip()
    return ""


def series_record(payload: dict[str, Any], row_id: int, columns: list[str]) -> dict[str, str]:
    date = str(payload.get("first_air_date") or "")
    genres = [item.get("name", "") for item in payload.get("genres", []) if isinstance(item, dict) and item.get("name")]
    creators = [item.get("name", "") for item in payload.get("created_by", []) if isinstance(item, dict) and item.get("name")]
    cast = [item.get("name", "") for item in ((payload.get("aggregate_credits") or {}).get("cast") or [])[:10] if isinstance(item, dict) and item.get("name")]
    episode_times = payload.get("episode_run_time") or []
    runtime = episode_times[0] if episode_times and isinstance(episode_times[0], (int, float)) else ""
    values: dict[str, Any] = {
        "id": row_id,
        "tmdb_id": payload["id"],
        "title": payload.get("name") or "",
        "year": date[:4] if date[:4].isdigit() else "",
        "rating": payload.get("vote_average", ""),
        "votes": payload.get("vote_count", ""),
        "genres": "|".join(dict.fromkeys(genres)),
        "language": payload.get("original_language") or "",
        "runtime": runtime,
        "certificate": content_rating(payload),
        "director": "|".join(dict.fromkeys(creators)),
        "cast": "|".join(dict.fromkeys(cast)),
        "description": payload.get("overview") or "",
        "tmdb_url": f"https://www.themoviedb.org/tv/{payload['id']}",
        "api_fields": SERIES_API_FIELDS,
        "selenium_fields": "",
        "poster_path": payload.get("poster_path") or "",
        "media_type": "tv",
    }
    return {column: "" if values.get(column) is None else str(values.get(column, "")) for column in columns}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    if not columns or "tmdb_id" not in columns or "poster_path" not in columns:
        raise SystemExit("The movie CSV is missing required columns; no changes were made.")
    if "media_type" not in columns:
        columns.append("media_type")
    before_count = len(rows)
    for row in rows:
        row.setdefault("media_type", "movie")
        if not row["media_type"]:
            row["media_type"] = "movie"
    original_tmdb_ids = [row.get("tmdb_id", "") for row in rows]
    if len(set(original_tmdb_ids)) != before_count:
        raise SystemExit("Existing TMDB IDs are not unique; no changes were made.")

    env = read_env()
    token = env.get("TMDB_ACCESS_TOKEN") or os.environ.get("TMDB_ACCESS_TOKEN", "")
    api_key = env.get("TMDB_API_KEY") or os.environ.get("TMDB_API_KEY", "")
    if not token and not api_key:
        raise SystemExit("TMDB_ACCESS_TOKEN or TMDB_API_KEY is missing; no changes were made.")
    headers = {"accept": "application/json", "User-Agent": "PopcornPicksCollegeProject/1.0"}
    params: dict[str, Any] = {"language": "en-US"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        params["api_key"] = api_key

    existing_by_tmdb = {row["tmdb_id"]: row for row in rows}
    existing_by_title = {normalized_title(row.get("title", "")): row for row in rows}
    additions: list[dict[str, str]] = []
    poster_updates: dict[str, str] = {}
    with requests.Session() as session:
        session.headers.update(headers)
        for title in FEATURED_SERIES:
            search = request_json(session, f"{API_BASE}/search/tv", {**params, "query": title, "include_adult": "false"}, 25)
            match = next((item for item in search.get("results", []) if normalized_title(str(item.get("name", ""))) == normalized_title(title)), None)
            if not match:
                raise SystemExit(f"TMDB did not return the exact TV series {title!r}; CSV not replaced.")
            tmdb_id = str(match["id"])
            detail = request_json(session, f"{API_BASE}/tv/{tmdb_id}", {**params, "append_to_response": "aggregate_credits,content_ratings"}, 25)
            if str(detail.get("id")) != tmdb_id or normalized_title(str(detail.get("name", ""))) != normalized_title(title):
                raise SystemExit(f"TMDB detail verification failed for {title!r}; CSV not replaced.")

            same_id = existing_by_tmdb.get(tmdb_id)
            same_title = existing_by_title.get(normalized_title(title))
            if same_id or same_title:
                existing = same_id or same_title
                if same_id is None or same_id is not existing or existing.get("media_type") != "tv":
                    raise SystemExit(f"A conflicting existing row matches {title!r}; CSV not replaced.")
                if not existing.get("poster_path", "").strip() and detail.get("poster_path"):
                    poster_updates[tmdb_id] = str(detail["poster_path"])
                continue

            additions.append(series_record(detail, 0, columns))
            time.sleep(0.1)

    next_id = max(int(row["id"]) for row in rows) + 1
    for offset, record in enumerate(additions):
        record["id"] = str(next_id + offset)
    final_rows = [dict(row) for row in rows]
    for tmdb_id, poster_path in poster_updates.items():
        next(row for row in final_rows if row["tmdb_id"] == tmdb_id)["poster_path"] = poster_path
    final_rows.extend(additions)

    for title in FEATURED_SERIES:
        if not any(normalized_title(row.get("title", "")) == normalized_title(title) and row.get("media_type") == "tv" for row in final_rows):
            raise SystemExit(f"Validation failed: {title!r} missing from TV records; CSV not replaced.")
    final_ids = [row["tmdb_id"] for row in final_rows]
    if len(final_ids) != len(set(final_ids)) or len(final_rows) != before_count + len(additions):
        raise SystemExit("Validation failed: duplicate IDs or wrong row count; CSV not replaced.")
    if any(any(row.get(column, "") != original.get(column, "") for column in reader.fieldnames or [] if not (column == "poster_path" and original.get("tmdb_id") in poster_updates)) for original, row in zip(rows, final_rows[:before_count])):
        raise SystemExit("Validation failed: an existing field changed; CSV not replaced.")

    if not BACKUP_PATH.exists():
        shutil.copy2(CSV_PATH, BACKUP_PATH)
    fd, temp_name = tempfile.mkstemp(prefix="movies.tv.", suffix=".csv.tmp", dir=CSV_PATH.parent)
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
            raise RuntimeError("Temporary CSV validation failed; original was not replaced.")
        os.replace(temp_name, CSV_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    print(f"Original rows: {before_count}")
    print(f"TV series added: {len(additions)}")
    for row in additions:
        print(f"{row['title']}: tmdb_id={row['tmdb_id']}, poster={'yes' if row['poster_path'] else 'no'}")
    print(f"Final rows: {len(final_rows)}; unique TMDB IDs: {len(set(final_ids))}")
    print(f"Backup: {BACKUP_PATH}")


if __name__ == "__main__":
    main()
