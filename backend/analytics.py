"""Pandas and NumPy analytics calculated from the Popcorn Picks database."""

import json

import numpy as np
import pandas as pd
from sqlalchemy import text

from database import db


MIN_VOTES = 100
MIN_CATEGORY_SIZE = 5
RECENT_YEARS = 10
TOP_N = 20


def load_movie_frame():
    """Load movie and normalized genre rows through SQLAlchemy."""
    query = text(
        """
        SELECT m.id, m.title, m.year, m.rating, m.votes, m.language,
               m.runtime, m.director, g.name AS genre
        FROM movies AS m
        LEFT JOIN movie_genres AS mg ON mg.movie_id = m.id
        LEFT JOIN genres AS g ON g.id = mg.genre_id
        """
    )
    frame = pd.read_sql_query(query, db.engine)
    for column in ("year", "rating", "votes", "runtime"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def movie_rows(frame):
    """Return one row per movie for movie-level statistics."""
    return frame.drop_duplicates(subset=["id"]).copy()


def records(frame, columns=None):
    selected = frame[columns] if columns else frame
    return json.loads(selected.replace({np.nan: None}).to_json(orient="records"))


def number(value, digits=None):
    if value is None or pd.isna(value):
        return None
    result = float(value)
    return round(result, digits) if digits is not None else result


def integer(value):
    if value is None or pd.isna(value):
        return None
    return int(value)


def distribution(frame, column):
    result = frame[column].dropna().value_counts().rename_axis(column).reset_index(name="count")
    return records(result, [column, "count"])


def rating_eligible(movies):
    return movies[movies["rating"].notna() & (movies["votes"].fillna(0) >= MIN_VOTES)]


def genre_frame(frame):
    return frame[frame["genre"].notna() & frame["genre"].ne("")].copy()


def genre_statistics(frame):
    genres = genre_frame(frame)
    grouped = genres.groupby("genre", as_index=False).agg(
        movies=("id", "nunique"),
        rated_movies=("rating", "count"),
        average_rating=("rating", "mean"),
    )
    grouped["average_rating"] = grouped["average_rating"].round(2)
    grouped = grouped.sort_values(["movies", "genre"], ascending=[False, True])
    averages = grouped[grouped["movies"] >= MIN_CATEGORY_SIZE].copy()
    return grouped, averages


def language_statistics(movies):
    languages = movies[movies["language"].notna() & movies["language"].ne("")]
    grouped = languages.groupby("language", as_index=False).agg(
        movies=("id", "count"),
        rated_movies=("rating", "count"),
        average_rating=("rating", "mean"),
    )
    grouped["average_rating"] = grouped["average_rating"].round(2)
    grouped = grouped.sort_values(["movies", "language"], ascending=[False, True])
    averages = grouped[grouped["movies"] >= MIN_CATEGORY_SIZE].copy()
    return grouped, averages


def top_movie_records(movies, limit=TOP_N):
    qualified = rating_eligible(movies)
    return records(
        qualified.sort_values(["rating", "votes", "title"], ascending=[False, False, True]).head(limit),
        ["id", "title", "year", "rating", "votes", "language"],
    )


def popular_movie_records(movies, limit=TOP_N):
    qualified = movies[movies["votes"].notna()]
    return records(
        qualified.sort_values(["votes", "rating", "title"], ascending=[False, False, True]).head(limit),
        ["id", "title", "year", "rating", "votes", "language"],
    )


def year_statistics(movies):
    dated = movies[movies["year"].notna()].copy()
    counts = dated.groupby("year", as_index=False).size().rename(columns={"size": "count"})
    averages = dated.groupby("year", as_index=False).agg(
        average_rating=("rating", "mean"), rated_movies=("rating", "count")
    )
    averages["average_rating"] = averages["average_rating"].round(2)
    return counts.sort_values("year"), averages.sort_values("year")


def summary(frame):
    movies = movie_rows(frame)
    rated = rating_eligible(movies)
    genres, genre_averages = genre_statistics(frame)
    languages, language_averages = language_statistics(movies)
    years, year_averages = year_statistics(movies)

    highest = rated.sort_values(["rating", "votes", "title"], ascending=[False, False, True]).head(1)
    common_genre = genres.iloc[0] if not genres.empty else None
    common_language = languages.iloc[0] if not languages.empty else None
    productive_year = years.sort_values(["count", "year"], ascending=[False, True]).head(1)
    movies["decade"] = (movies["year"] // 10 * 10).astype("Int64")
    decades = movies.dropna(subset=["decade"]).groupby("decade").size().sort_values(ascending=False)
    recent_cutoff = int(movies["year"].max() - RECENT_YEARS) if movies["year"].notna().any() else None
    recent = rated[rated["year"] >= recent_cutoff] if recent_cutoff is not None else rated.iloc[0:0]
    best_language = language_averages.sort_values(["average_rating", "rated_movies", "language"], ascending=[False, False, True]).head(1)

    highest_movie = highest.iloc[0] if not highest.empty else None
    return {
        "total_movies": int(len(movies)),
        "average_rating": number(movies["rating"].mean(), 2),
        "rating_movie_count": int(movies["rating"].notna().sum()),
        "minimum_votes_for_rankings": MIN_VOTES,
        "minimum_category_size": MIN_CATEGORY_SIZE,
        "highest_rated_movie": None if highest_movie is None else {
            "id": integer(highest_movie["id"]), "title": highest_movie["title"],
            "year": integer(highest_movie["year"]), "rating": number(highest_movie["rating"], 2),
            "votes": integer(highest_movie["votes"]),
        },
        "most_common_genre": None if common_genre is None else {"name": common_genre["genre"], "movies": int(common_genre["movies"])},
        "most_represented_language": None if common_language is None else {"name": common_language["language"], "movies": int(common_language["movies"])},
        "most_productive_year": None if productive_year.empty else {"year": integer(productive_year.iloc[0]["year"]), "movies": int(productive_year.iloc[0]["count"])},
        "most_common_release_decade": None if decades.empty else {"decade": integer(decades.index[0]), "movies": int(decades.iloc[0])},
        "best_rated_language": None if best_language.empty else {"name": best_language.iloc[0]["language"], "average_rating": number(best_language.iloc[0]["average_rating"], 2), "movies": int(best_language.iloc[0]["movies"]), "rated_movies": int(best_language.iloc[0]["rated_movies"])},
        "recent_year_cutoff": recent_cutoff,
        "highest_rated_recent_movies": top_movie_records(recent),
        "top_rated_movies": top_movie_records(movies),
        "popular_movies": popular_movie_records(movies),
        "movies_by_year": records(years, ["year", "count"]),
        "average_rating_by_year": records(year_averages, ["year", "average_rating", "rated_movies"]),
        "genre_distribution": records(genres, ["genre", "movies"]),
        "average_rating_by_genre": records(genre_averages, ["genre", "average_rating", "rated_movies", "movies"]),
        "language_distribution": records(languages, ["language", "movies"]),
        "rating_distribution": rating_distribution(movies),
        "popular_genres": records(genres.head(TOP_N), ["genre", "movies"]),
        "genre_trends_over_time": genre_trends(frame),
    }


def rating_distribution(movies):
    rated = movies[movies["rating"].notna()].copy()
    bins = np.arange(0, 11, 1)
    labels = [f"{start}-{start + 1}" for start in range(0, 10)]
    rated["rating_band"] = pd.cut(rated["rating"], bins=bins, labels=labels, right=False, include_lowest=True)
    result = rated["rating_band"].value_counts().reindex(labels, fill_value=0).rename_axis("rating_band").reset_index(name="count")
    return records(result, ["rating_band", "count"])


def genre_trends(frame):
    trends = genre_frame(frame)
    trends = trends[trends["year"].notna()].groupby(["year", "genre"], as_index=False).size().rename(columns={"size": "count"})
    return records(trends.sort_values(["year", "genre"]), ["year", "genre", "count"])


def trending(frame):
    """Rank recent movies using rating, popularity, and recency.

    The score is deterministic: 45% normalized rating, 30% normalized
    log(votes), and 25% exponential recency decay. Movies need at least
    MIN_VOTES votes so a sparsely rated title cannot dominate the list.
    """
    movies = movie_rows(frame)
    latest_year = int(movies["year"].max()) if movies["year"].notna().any() else None
    recent = movies[movies["year"] >= latest_year - RECENT_YEARS] if latest_year is not None else movies.iloc[0:0]
    recent = recent[recent["rating"].notna() & (recent["votes"].fillna(0) >= MIN_VOTES)].copy()
    recent["rating_component"] = recent["rating"] / 10
    recent["popularity_component"] = np.log1p(recent["votes"])
    max_log_votes = recent["popularity_component"].max()
    recent["popularity_component"] = recent["popularity_component"] / max_log_votes if max_log_votes else 0
    recent["recency_component"] = np.exp(-(latest_year - recent["year"]) / RECENT_YEARS)
    recent["trend_score"] = (
        0.45 * recent["rating_component"]
        + 0.30 * recent["popularity_component"]
        + 0.25 * recent["recency_component"]
    )
    recent = recent.sort_values(["trend_score", "votes", "title"], ascending=[False, False, True]).head(TOP_N)
    return {
        "definition": "Recent movies ranked by 45% rating, 30% normalized log1p(votes), and 25% exponential recency.",
        "recent_year_cutoff": None if latest_year is None else latest_year - RECENT_YEARS,
        "minimum_votes": MIN_VOTES,
        "weights": {"rating": 0.45, "popularity": 0.30, "recency": 0.25},
        "data": records(recent, ["id", "title", "year", "rating", "votes", "language", "trend_score", "rating_component", "popularity_component", "recency_component"]),
    }


def recommendations(frame, movie_id, limit=10):
    """Recommend movies with a transparent content-similarity score."""
    movies = movie_rows(frame)
    selected = movies[movies["id"] == movie_id]
    if selected.empty:
        raise LookupError("Movie not found.")
    source = selected.iloc[0]
    source_genres = set(frame[frame["id"] == movie_id]["genre"].dropna())
    candidates = movies[movies["id"] != movie_id].copy()
    candidates["shared_genre_count"] = 0
    if source_genres:
        shared = (
            frame[frame["genre"].isin(source_genres)][["id", "genre"]]
            .drop_duplicates()
            .query("id != @movie_id")
            .groupby("id")["genre"]
            .nunique()
        )
        candidates["shared_genre_count"] = candidates["id"].map(shared).fillna(0)
    candidates["genre_component"] = candidates["shared_genre_count"] / max(len(source_genres), 1)
    candidates["language_component"] = np.where(
        pd.notna(source["language"]) & candidates["language"].notna() & (candidates["language"] == source["language"]), 1.0, 0.0
    )
    candidates["rating_component"] = np.where(
        pd.notna(source["rating"]) & candidates["rating"].notna(),
        np.maximum(0, 1 - (np.abs(candidates["rating"] - source["rating"]) / 4)),
        0.0,
    )
    candidates["period_component"] = np.where(
        pd.notna(source["year"]) & candidates["year"].notna(),
        np.maximum(0, 1 - (np.abs(candidates["year"] - source["year"]) / 20)),
        0.0,
    )
    candidates["recommendation_score"] = (
        0.50 * candidates["genre_component"]
        + 0.20 * candidates["language_component"]
        + 0.15 * candidates["rating_component"]
        + 0.15 * candidates["period_component"]
    )
    candidates = candidates[candidates["recommendation_score"] > 0].copy()
    candidates = candidates.sort_values(
        ["recommendation_score", "votes", "rating", "title"],
        ascending=[False, False, False, True],
        na_position="last",
    ).head(limit)
    candidates["shared_genres"] = candidates["id"].map(
        frame[frame["genre"].isin(source_genres)].groupby("id")["genre"].apply(list)
        if source_genres else {}
    ).apply(lambda genres: genres if isinstance(genres, list) else [])
    candidates["match_reasons"] = candidates.apply(
        lambda row: [
            reason for reason, matched in (
                ("shared_genre", row["shared_genre_count"] > 0),
                ("same_language", row["language_component"] == 1),
                ("similar_rating", row["rating_component"] >= 0.75),
                ("similar_release_period", row["period_component"] >= 0.75),
            ) if matched
        ],
        axis=1,
    )
    result = records(candidates, ["id", "title", "year", "rating", "votes", "language", "recommendation_score", "shared_genres", "match_reasons"])
    return {
        "definition": "50% shared genres, 20% same language, 15% rating similarity, and 15% release-period similarity.",
        "source_movie_id": movie_id,
        "weights": {"shared_genres": 0.50, "same_language": 0.20, "similar_rating": 0.15, "similar_release_period": 0.15},
        "data": result,
    }
