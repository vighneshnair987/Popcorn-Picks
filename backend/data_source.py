"""Pandas-backed movie data source for the production TMDB CSV."""

from __future__ import annotations

import numbers
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR.parent / "data" / "movies.csv"
NUMERIC_COLUMNS = ["id", "tmdb_id", "year", "rating", "votes", "runtime", "series_tmdb_id", "season_number"]
MULTI_VALUE_COLUMNS = ["genres", "cast"]


def load_movies() -> pd.DataFrame:
    frame = pd.read_csv(CSV_PATH, keep_default_na=False)
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in MULTI_VALUE_COLUMNS:
        frame[column] = frame[column].map(split_values)
    return frame


def split_values(value) -> list[str]:
    if value is None or pd.isna(value) or value == "":
        return []
    return [item.strip() for item in str(value).split("|") if item.strip()]


def scalar(value):
    if pd.isna(value):
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real) and float(value).is_integer():
        return int(value)
    return float(value) if isinstance(value, numbers.Real) else value


def movie_to_dict(movie: pd.Series) -> dict:
    return {
        "id": scalar(movie["id"]),
        "title": movie["title"],
        "year": scalar(movie["year"]),
        "rating": scalar(movie["rating"]),
        "votes": scalar(movie["votes"]),
        "genres": movie["genres"],
        "language": movie["language"] or None,
        "runtime": scalar(movie["runtime"]),
        "certificate": movie["certificate"] or None,
        "director": movie["director"] or None,
        "cast": movie["cast"],
        "description": movie["description"] or None,
        "poster_path": movie.get("poster_path") or None,
        "media_type": movie.get("media_type") or "movie",
        "series_tmdb_id": scalar(movie.get("series_tmdb_id")),
        "season_number": scalar(movie.get("season_number")),
    }


def analytics_frame() -> pd.DataFrame:
    frame = load_movies()
    if "media_type" in frame.columns:
        frame = frame[frame["media_type"].eq("movie")]
    return frame.explode("genres").rename(columns={"genres": "genre"})[
        ["id", "title", "year", "rating", "votes", "language", "runtime", "director", "genre", "poster_path"]
    ]
