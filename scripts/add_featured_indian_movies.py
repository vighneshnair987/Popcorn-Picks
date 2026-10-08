"""Add a small verified set of Indian films to the existing movie CSV."""

from __future__ import annotations

import argparse
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
BACKUP_PATH = ROOT / "data" / "movies_backup_before_featured_additions.csv"
ENV_PATH = ROOT / ".env"
API_BASE = "https://api.themoviedb.org/3"

# Quotas span the requested Indian film industries/languages. Candidates are
# selected dynamically by TMDB vote count; no movie title list is embedded.
LANGUAGE_TARGETS = {"hi": 7, "ml": 5, "ta": 5, "te": 5, "kn": 3, "bn": 2, "mr": 2, "pa": 1}
MANDATORY_TITLE = "Manjummel Boys"
DETAIL_APPEND = "credits,release_dates"
SCRIPT_API_FIELDS = "title|year|rating|votes|genres|language|runtime|certificate|director|cast|description|poster_path"


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
                time.sleep(2 ** attempt)
                continue
            break
        if response.status_code == 429 and attempt < 3:
            retry_after = response.headers.get("Retry-After", "2")
            try:
                time.sleep(max(1.0, float(retry_after)))
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


def split_pipe(value: Any) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split("|") if part.strip()]


def normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def has_indian_production(payload: dict[str, Any]) -> bool:
    return any(country.get("iso_3166_1") == "IN" for country in payload.get("production_countries", []) if isinstance(country, dict))


def certificate_from_detail(payload: dict[str, Any]) -> str:
    release_results = (payload.get("release_dates") or {}).get("results", [])
    candidates: list[tuple[int, str]] = []
    for country in release_results:
        if not isinstance(country, dict):
            continue
        priority = 0 if country.get("iso_3166_1") == "US" else 1
        for release in country.get("release_dates", []):
            if isinstance(release, dict) and release.get("certification"):
                candidates.append((priority, str(release["certification"]).strip()))
    return min(candidates)[1] if candidates else ""


def movie_record(payload: dict[str, Any], new_id: int, columns: list[str]) -> dict[str, str]:
    credits = payload.get("credits") or {}
    directors = [
        person.get("name", "")
        for person in credits.get("crew", [])
        if isinstance(person, dict) and person.get("job") == "Director" and person.get("name")
    ]
    release_date = str(payload.get("release_date") or "")
    genres = [genre.get("name", "") for genre in payload.get("genres", []) if isinstance(genre, dict) and genre.get("name")]
    cast = [person.get("name", "") for person in (credits.get("cast") or [])[:10] if isinstance(person, dict) and person.get("name")]
    record: dict[str, Any] = {
        "id": new_id,
        "tmdb_id": payload.get("id", ""),
        "title": payload.get("title") or "",
        "year": release_date[:4] if release_date[:4].isdigit() else "",
        "rating": payload.get("vote_average", ""),
        "votes": payload.get("vote_count", ""),
        "genres": "|".join(dict.fromkeys(genres)),
        "language": payload.get("original_language") or "",
        "runtime": payload.get("runtime", ""),
        "certificate": certificate_from_detail(payload),
        "director": "|".join(dict.fromkeys(directors)),
        "cast": "|".join(dict.fromkeys(cast)),
        "description": payload.get("overview") or "",
        "tmdb_url": f"https://www.themoviedb.org/movie/{payload.get('id')}",
        "api_fields": SCRIPT_API_FIELDS,
        "selenium_fields": "",
        "poster_path": payload.get("poster_path") or "",
    }
    return {column: "" if record.get(column) is None else str(record.get(column, "")) for column in columns}


def load_csv() -> tuple[list[str], list[dict[str, str]]]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError("data/movies.csv has no header.")
        rows = list(reader)
        return list(reader.fieldnames), rows


def detail_for(session: requests.Session, tmdb_id: int, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    return request_json(session, f"{API_BASE}/movie/{tmdb_id}", {**params, "append_to_response": DETAIL_APPEND}, timeout)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.12, help="Seconds between TMDB calls (default: 0.12).")
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()

    columns, original_rows = load_csv()
    original_count = len(original_rows)
    required_columns = {"id", "tmdb_id", "title", "year", "rating", "votes", "genres", "language", "runtime", "certificate", "director", "cast", "description", "tmdb_url", "api_fields", "selenium_fields", "poster_path"}
    if not required_columns.issubset(columns):
        raise SystemExit("The CSV does not match the existing movie schema; no changes were made.")
    ids = [int(row["tmdb_id"]) for row in original_rows if row.get("tmdb_id", "").isdigit()]
    if len(ids) != original_count or len(set(ids)) != original_count:
        raise SystemExit("The existing CSV has missing or duplicate TMDB IDs; no changes were made.")

    env = read_env()
    token = env.get("TMDB_ACCESS_TOKEN") or os.environ.get("TMDB_ACCESS_TOKEN", "")
    api_key = env.get("TMDB_API_KEY") or os.environ.get("TMDB_API_KEY", "")
    if not token and not api_key:
        raise SystemExit("TMDB_ACCESS_TOKEN or TMDB_API_KEY is missing; no changes were made.")
    headers = {"accept": "application/json", "User-Agent": "PopcornPicksCollegeProject/1.0"}
    request_params: dict[str, Any] = {"language": "en-US"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        request_params["api_key"] = api_key

    existing_ids = set(ids)
    existing_titles = {normalized_title(row.get("title", "")): row for row in original_rows}
    prior_additions = [row for row in original_rows if row.get("api_fields") == SCRIPT_API_FIELDS]
    new_records: list[dict[str, str]] = []
    poster_updates: dict[str, str] = {}
    added_ids: set[int] = set()
    verified_indian_ids: set[int] = set()
    counts = {language: 0 for language in LANGUAGE_TARGETS}
    for row in prior_additions:
        if row.get("language") in counts:
            counts[row["language"]] += 1
    details_requested = 0

    with requests.Session() as session:
        session.headers.update(headers)

        # Find the mandatory title dynamically, then verify it from TMDB detail.
        search = request_json(session, f"{API_BASE}/search/movie", {**request_params, "query": MANDATORY_TITLE, "include_adult": "false"}, args.timeout)
        exact = [item for item in search.get("results", []) if normalized_title(str(item.get("title", ""))) == normalized_title(MANDATORY_TITLE)]
        if not exact:
            raise SystemExit("TMDB did not return the required Manjummel Boys title; no CSV changes were made.")
        manjummel_id = int(exact[0]["id"])
        existing_manjummel = existing_titles.get(normalized_title(MANDATORY_TITLE))
        if manjummel_id not in existing_ids and existing_manjummel is None:
            detail = detail_for(session, manjummel_id, request_params, args.timeout)
            details_requested += 1
            if not has_indian_production(detail):
                raise SystemExit("TMDB does not verify Manjummel Boys as an Indian production; no CSV changes were made.")
            if args.delay > 0:
                time.sleep(args.delay)
            record = movie_record(detail, 0, columns)
            if normalized_title(record["title"]) != normalized_title(MANDATORY_TITLE):
                raise SystemExit("The TMDB detail title did not match Manjummel Boys; no CSV changes were made.")
            new_records.append(record)
            added_ids.add(manjummel_id)
            verified_indian_ids.add(manjummel_id)
            counts[record["language"]] = counts.get(record["language"], 0) + 1
        elif existing_manjummel and existing_manjummel.get("tmdb_id") == str(manjummel_id) and not existing_manjummel.get("poster_path", "").strip():
            poster_updates[str(manjummel_id)] = str(exact[0].get("poster_path") or "")

        # Pull only as many candidates per requested film language as needed.
        for language, target in LANGUAGE_TARGETS.items():
            while counts.get(language, 0) < target:
                filled_page = False
                for page in range(1, 4):
                    discovery = request_json(
                        session,
                        f"{API_BASE}/discover/movie",
                        {
                            **request_params,
                            "with_origin_country": "IN",
                            "with_original_language": language,
                            "sort_by": "vote_count.desc",
                            "include_adult": "false",
                            "page": page,
                        },
                        args.timeout,
                    )
                    candidates = sorted(discovery.get("results", []), key=lambda item: int(item.get("vote_count") or 0), reverse=True)
                    for candidate in candidates:
                        tmdb_id = int(candidate.get("id") or 0)
                        if not tmdb_id or tmdb_id in existing_ids or tmdb_id in added_ids:
                            continue
                        if normalized_title(str(candidate.get("title", ""))) in existing_titles:
                            continue
                        detail = detail_for(session, tmdb_id, request_params, args.timeout)
                        details_requested += 1
                        if not has_indian_production(detail) or detail.get("original_language") != language:
                            continue
                        record = movie_record(detail, 0, columns)
                        # TMDB may return the same item for different language queries; ID and
                        # country checks keep the dataset de-duplicated and genuinely Indian.
                        if tmdb_id in added_ids:
                            continue
                        new_records.append(record)
                        added_ids.add(tmdb_id)
                        verified_indian_ids.add(tmdb_id)
                        counts[language] = counts.get(language, 0) + 1
                        filled_page = True
                        if args.delay > 0:
                            time.sleep(args.delay)
                        if counts[language] >= target:
                            break
                    if counts.get(language, 0) >= target or page == 3:
                        break
                    if not candidates:
                        break
                if counts.get(language, 0) >= target:
                    break
                # At the available targeted pages, stop short rather than crawl broadly.
                break

    # If Manjummel already existed, it is valid only when its existing production
    # record is already present; existing records are otherwise left untouched.
    final_rows = [dict(row) for row in original_rows]
    for tmdb_id, poster_path in poster_updates.items():
        for row in final_rows:
            if row["tmdb_id"] == tmdb_id and not row.get("poster_path", "").strip():
                row["poster_path"] = poster_path
                break
    next_id = max(int(row["id"]) for row in original_rows) + 1
    for offset, record in enumerate(new_records):
        record["id"] = str(next_id + offset)
        final_rows.append(record)

    indian_count = len(new_records)
    if not any(normalized_title(row["title"]) == normalized_title(MANDATORY_TITLE) for row in final_rows):
        raise SystemExit("Validation failed: Manjummel Boys would be absent; CSV not replaced.")
    final_tmdb_ids = [row["tmdb_id"] for row in final_rows]
    if len(final_tmdb_ids) != len(set(final_tmdb_ids)):
        raise SystemExit("Validation failed: duplicate TMDB IDs; CSV not replaced.")
    if len(final_rows) != original_count + indian_count:
        raise SystemExit("Validation failed: row count mismatch; CSV not replaced.")
    changed_existing_fields = any(
        any(
            row.get(column, "") != original.get(column, "")
            for column in columns
            if not (column == "poster_path" and original.get("tmdb_id") in poster_updates and not original.get("poster_path", "").strip())
        )
        for original, row in zip(original_rows, final_rows[:original_count])
    )
    if changed_existing_fields:
        raise SystemExit("Validation failed: an existing field changed; CSV not replaced.")
    if verified_indian_ids != added_ids:
        raise SystemExit("Validation failed: non-Indian record; CSV not replaced.")
    if counts.get("ml", 0) == 0 and not any(normalized_title(row["title"]) == normalized_title(MANDATORY_TITLE) for row in original_rows):
        raise SystemExit("Validation failed: Manjummel Boys is not counted among verified new Indian films; CSV not replaced.")

    # The table has no media_type/origin field and its loader/analytics treat every
    # row as a movie. Do not insert Stranger Things or IT: Welcome to Derry TV data.
    if not BACKUP_PATH.exists():
        shutil.copy2(CSV_PATH, BACKUP_PATH)

    fd, temp_name = tempfile.mkstemp(prefix="movies.featured.", suffix=".csv.tmp", dir=CSV_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as target_file:
            writer = csv.DictWriter(target_file, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(final_rows)
            target_file.flush()
            os.fsync(target_file.fileno())
        with open(temp_name, "r", encoding="utf-8", newline="") as check_file:
            verified = list(csv.DictReader(check_file))
        if len(verified) != len(final_rows) or len({row["tmdb_id"] for row in verified}) != len(verified):
            raise RuntimeError("Temporary CSV validation failed; original was not replaced.")
        os.replace(temp_name, CSV_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    indian_with_posters = sum(bool(row.get("poster_path", "").strip()) for row in new_records)
    print(f"Original rows: {original_count}")
    print(f"New Indian movies added this run: {indian_count}")
    print(f"Previously added by this script: {len(prior_additions)}")
    print(f"Indian movies by TMDB original language: {counts}")
    print("New Indian titles:")
    for record in new_records:
        print(f"  {record['title']} (tmdb_id={record['tmdb_id']}, language={record['language']}, poster={'yes' if record['poster_path'] else 'no'})")
    print(f"Manjummel Boys: {'present' if any(normalized_title(row['title']) == normalized_title(MANDATORY_TITLE) for row in final_rows) else 'missing'}")
    print("Featured TV titles added: 0 (the current CSV/API schema is movie-only).")
    print(f"New titles with poster_path: {indian_with_posters}")
    print(f"New titles without poster_path: {indian_count - indian_with_posters}")
    print(f"TMDB detail requests: {details_requested}")
    print(f"Final rows: {len(final_rows)}")
    print(f"Duplicate TMDB IDs: {len(final_tmdb_ids) - len(set(final_tmdb_ids))}")
    print(f"Backup: {BACKUP_PATH}")


if __name__ == "__main__":
    main()
