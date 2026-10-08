"""Clean and validate collected IMDb movie CSV data."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import pandas as pd

MOVIE_COLUMNS = [
    "imdb_id", "title", "year", "runtime", "genres", "rating", "votes",
    "certificate", "director", "cast", "description", "language", "imdb_url",
]
NUMERIC_COLUMNS = ["year", "runtime", "rating", "votes"]
MULTI_VALUE_COLUMNS = ["genres", "cast"]
IMDB_ID_PATTERN = re.compile(r"^tt\d+$")


def clean_text(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def clean_multi_value(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    values = []
    for item in re.split(r"\s*[|,]\s*", text):
        item = clean_text(item)
        if item and item not in values:
            values.append(item)
    return "|".join(values) if values else None


def clean_movies(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.rename(columns=lambda column: str(column).strip().lower())
    for column in MOVIE_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame = frame[MOVIE_COLUMNS].copy()
    for column in frame.columns:
        frame[column] = frame[column].map(clean_text)
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in MULTI_VALUE_COLUMNS:
        frame[column] = frame[column].map(clean_multi_value)
    frame["imdb_id"] = frame["imdb_id"].where(frame["imdb_id"].str.match(IMDB_ID_PATTERN, na=False))
    frame["title"] = frame["title"].map(clean_text)
    frame = frame.dropna(subset=["imdb_id", "title"])
    frame = frame.drop_duplicates(subset=["imdb_id"], keep="first")
    frame = frame[(frame["title"].str.len() > 0) & (frame["imdb_id"].str.len() > 2)]
    frame["year"] = frame["year"].where(frame["year"].between(1800, 2100))
    frame["rating"] = frame["rating"].where(frame["rating"].between(0, 10))
    frame["runtime"] = frame["runtime"].where(frame["runtime"] > 0)
    frame["votes"] = frame["votes"].where(frame["votes"] >= 0)
    return frame.reset_index(drop=True)


def clean_csv(input_path: str | Path, output_path: str | Path) -> pd.DataFrame:
    frame = clean_movies(pd.read_csv(input_path))
    frame.to_csv(output_path, index=False)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean a collected IMDb CSV file.")
    parser.add_argument("input", default="data/raw_test.csv", nargs="?")
    parser.add_argument("output", default="data/clean_test.csv", nargs="?")
    args = parser.parse_args()
    cleaned = clean_csv(args.input, args.output)
    print(f"Cleaned {len(cleaned)} records into {args.output}")


if __name__ == "__main__":
    main()
