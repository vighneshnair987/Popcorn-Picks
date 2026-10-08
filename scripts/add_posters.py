"""Resume-safe TMDB poster enrichment for the existing Popcorn Picks CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tempfile
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "movies.csv"
CHECKPOINT_PATH = ROOT / "data" / ".poster_checkpoint.json"
ENV_PATH = ROOT / ".env"
TMDB_MOVIE_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}"
POSTER_PATH_PATTERN = re.compile(r"^/[^/\\]+$")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def atomic_checkpoint(state: dict[str, str]) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = CHECKPOINT_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, CHECKPOINT_PATH)


def poster_for(session: requests.Session, tmdb_id: str, timeout: int) -> str:
    response = session.get(TMDB_MOVIE_URL.format(tmdb_id=tmdb_id), timeout=timeout)
    if response.status_code == 429:
        delay = response.headers.get("Retry-After", "2")
        try:
            wait = max(1.0, float(delay))
        except ValueError:
            wait = 2.0
        time.sleep(wait)
        response = session.get(TMDB_MOVIE_URL.format(tmdb_id=tmdb_id), timeout=timeout)
    if response.status_code == 404:
        return ""
    response.raise_for_status()
    value = response.json().get("poster_path") or ""
    return value if isinstance(value, str) and POSTER_PATH_PATTERN.fullmatch(value) else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.15, help="Seconds between TMDB requests (default: 0.15).")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    env = read_env(ENV_PATH)
    token = env.get("TMDB_ACCESS_TOKEN", "") or os.environ.get("TMDB_ACCESS_TOKEN", "")
    api_key = env.get("TMDB_API_KEY", "") or os.environ.get("TMDB_API_KEY", "")
    if not token and not api_key:
        raise SystemExit("TMDB_ACCESS_TOKEN or TMDB_API_KEY must be configured in .env or the environment.")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames or []
        if "tmdb_id" not in fieldnames:
            raise SystemExit("data/movies.csv is missing its tmdb_id column.")
        rows = list(reader)
    initial_count = len(rows)
    original_fields = list(fieldnames)
    if "poster_path" not in fieldnames:
        fieldnames.append("poster_path")
    for row in rows:
        row.setdefault("poster_path", "")

    try:
        checkpoint = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8")) if CHECKPOINT_PATH.exists() else {}
    except (OSError, json.JSONDecodeError):
        checkpoint = {}
    if not isinstance(checkpoint, dict):
        checkpoint = {}

    already_had = sum(bool(row.get("poster_path", "").strip()) for row in rows)
    request_count = 0
    found = already_had
    unavailable = 0
    headers = {"accept": "application/json", "User-Agent": "PopcornPicksCollegeProject/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {} if token else {"api_key": api_key}

    with requests.Session() as session:
        session.headers.update(headers)
        for index, row in enumerate(rows, start=1):
            tmdb_id = str(row.get("tmdb_id", "")).strip()
            if row.get("poster_path", "").strip():
                continue
            if tmdb_id in checkpoint:
                row["poster_path"] = checkpoint[tmdb_id] or ""
            elif tmdb_id.isdigit():
                try:
                    # TMDB accepts the API key in the query string; requests keeps it out of logs/output.
                    path = poster_for(session, tmdb_id, args.timeout) if token else poster_for(
                        KeyParamSession(session, params), tmdb_id, args.timeout
                    )
                    request_count += 1
                    checkpoint[tmdb_id] = path
                    row["poster_path"] = path
                    atomic_checkpoint(checkpoint)
                    if args.delay > 0:
                        time.sleep(args.delay)
                except requests.RequestException as exc:
                    print(f"Request failed for tmdb_id={tmdb_id} ({type(exc).__name__}); progress is checkpointed, rerun to resume.", flush=True)
                    atomic_checkpoint(checkpoint)
                    raise SystemExit(1) from exc
            else:
                checkpoint[tmdb_id] = ""
                row["poster_path"] = ""
                atomic_checkpoint(checkpoint)
            if row["poster_path"]:
                found += 1
            else:
                unavailable += 1
            if index % 50 == 0 or index == len(rows):
                print(f"Processed {index}/{len(rows)} rows; posters found={found}, unavailable={unavailable}, requests={request_count}.", flush=True)

    if len(rows) != initial_count:
        raise SystemExit("Row count changed in memory; original CSV was not replaced.")
    fd, temp_name = tempfile.mkstemp(prefix="movies.", suffix=".csv.tmp", dir=CSV_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            target.flush()
            os.fsync(target.fileno())
        with open(temp_name, "r", encoding="utf-8", newline="") as check:
            verified_rows = sum(1 for _ in csv.DictReader(check))
        if verified_rows != initial_count:
            raise RuntimeError(f"Temporary CSV row count {verified_rows} did not match {initial_count}.")
        os.replace(temp_name, CSV_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    print("Final summary:")
    print(f"Total movies: {initial_count}")
    print(f"Posters found: {found}")
    print(f"Posters unavailable: {unavailable}")
    print(f"Already had posters: {already_had}")
    print(f"TMDB requests made: {request_count}")
    print(f"Rows preserved: {initial_count}")


class KeyParamSession:
    """Add the configured v3 API key to a request without exposing it in source."""

    def __init__(self, session: requests.Session, params: dict[str, str]):
        self.session = session
        self.params = params

    def get(self, url: str, **kwargs):
        kwargs["params"] = {**self.params, **kwargs.get("params", {})}
        return self.session.get(url, **kwargs)


if __name__ == "__main__":
    main()
