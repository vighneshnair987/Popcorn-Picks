"""Flask REST API for Popcorn Picks."""

import os
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests

from analytics import (
    genre_statistics,
    genre_trends,
    language_statistics,
    load_movie_frame,
    recommendations,
    records,
    summary,
    trending,
    year_statistics,
)
from database import db, get_database_uri
from data_source import load_movies, movie_to_dict


SORTS = {
    "rating_desc": ("rating", False),
    "rating_asc": ("rating", True),
    "votes_desc": ("votes", False),
    "votes_asc": ("votes", True),
    "year_desc": ("year", False),
    "year_asc": ("year", True),
    "title_asc": ("title", True),
    "title_desc": ("title", False),
}


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri(db_path)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    CORS(app)

    @app.errorhandler(404)
    def not_found(_error):
        return error_response("Resource not found.", 404)

    @app.errorhandler(405)
    def method_not_allowed(_error):
        return error_response("HTTP method not allowed.", 405)

    @app.get("/api/movies")
    def list_movies():
        try:
            filters = parse_movie_filters(request.args)
            query = build_movie_query(filters)
            return paginated_response(query, filters["page"], filters["limit"])
        except ValueError as exc:
            return error_response(str(exc), 400)

    @app.get("/api/movies/<movie_id>")
    def get_movie(movie_id):
        try:
            parsed_id = parse_integer(movie_id, "id", minimum=1)
            movies = load_movies()
            matches = movies[movies["id"] == parsed_id]
            if matches.empty:
                return error_response("Movie not found.", 404)
            return jsonify({"success": True, "data": movie_to_dict(matches.iloc[0])})
        except ValueError as exc:
            return error_response(str(exc), 400)

    @app.get("/api/movies/<int:movie_id>/faceoff-metadata")
    def movie_faceoff_metadata(movie_id):
        movies = load_movies()
        matches = movies[movies["id"] == movie_id]
        if matches.empty:
            return error_response("Movie not found.", 404)

        movie = matches.iloc[0]
        tmdb_id = int(movie["tmdb_id"])
        media_type = movie.get("media_type", "movie") or "movie"
        if media_type == "tv_season":
            tmdb_url = f"https://api.themoviedb.org/3/tv/{int(movie['series_tmdb_id'])}/season/{int(movie['season_number'])}"
        else:
            tmdb_url = f"https://api.themoviedb.org/3/{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}"
        token = os.environ.get("TMDB_ACCESS_TOKEN", "")
        if not token:
            env_path = Path(__file__).resolve().parent.parent / ".env"
            if env_path.exists():
                for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if line.startswith("TMDB_ACCESS_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not token:
            return error_response("TMDB metadata could not be loaded.", 502)

        try:
            response = requests.get(
                tmdb_url,
                headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            app.logger.warning("TMDB face-off metadata request failed for movie %s: %s", tmdb_id, exc)
            return error_response("TMDB metadata could not be loaded.", 502)

        return jsonify({"success": True, "data": {
            "poster_path": payload.get("poster_path"),
            "popularity": payload.get("popularity"),
        }})

    @app.get("/api/movies/<int:movie_id>/watch-providers")
    def movie_watch_providers(movie_id):
        movies = load_movies()
        matches = movies[movies["id"] == movie_id]
        if matches.empty:
            return error_response("Movie not found.", 404)

        movie = matches.iloc[0]
        tmdb_id = int(movie["tmdb_id"])
        media_type = movie.get("media_type", "movie") or "movie"
        provider_id = int(movie["series_tmdb_id"]) if media_type == "tv_season" else tmdb_id
        provider_type = "tv" if media_type in {"tv", "tv_season"} else "movie"
        token = os.environ.get("TMDB_ACCESS_TOKEN", "")
        if not token:
            env_path = Path(__file__).resolve().parent.parent / ".env"
            if env_path.exists():
                for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if line.startswith("TMDB_ACCESS_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not token:
            app.logger.error("TMDB access token is not configured.")
            return error_response("Watch availability could not be loaded.", 502)

        try:
            response = requests.get(
                f"https://api.themoviedb.org/3/{provider_type}/{provider_id}/watch/providers",
                headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            app.logger.warning("TMDB watch-provider request failed for movie %s: %s", tmdb_id, exc)
            return error_response("Watch availability could not be loaded.", 502)

        india = payload.get("results", {}).get("IN") or {}
        return jsonify({
            "success": True,
            "data": {
                "link": india.get("link"),
                "flatrate": india.get("flatrate", []),
                "rent": india.get("rent", []),
                "buy": india.get("buy", []),
            },
        })

    @app.get("/api/search")
    def search_movies():
        try:
            args = request.args.copy()
            query_text = args.get("q", args.get("search", "")).strip()
            if not query_text:
                raise ValueError("Query parameter 'q' is required and cannot be empty.")
            args["search"] = query_text
            filters = parse_movie_filters(args)
            query = build_movie_query(filters)
            return paginated_response(query, filters["page"], filters["limit"])
        except ValueError as exc:
            return error_response(str(exc), 400)

    @app.get("/api/genres")
    def list_genres():
        movies = load_movies()
        names = sorted({genre for genres in movies["genres"] for genre in genres})
        return jsonify({"success": True, "data": names})

    @app.get("/api/languages")
    def list_languages():
        movies = load_movies()
        languages = sorted({language for language in movies["language"] if language})
        return jsonify({"success": True, "data": languages})

    @app.get("/api/analytics")
    def analytics_overview():
        return jsonify({"success": True, "data": summary(load_movie_frame())})

    @app.get("/api/trending")
    def trending_movies():
        return jsonify({"success": True, "data": trending(load_movie_frame())})

    @app.get("/api/recommendations")
    def movie_recommendations():
        try:
            movie_id = parse_integer(request.args.get("movie_id", request.args.get("id")), "movie_id", minimum=1)
            limit = parse_integer(request.args.get("limit", 10), "limit", minimum=1, maximum=50)
            return jsonify({"success": True, "data": recommendations(load_movie_frame(), movie_id, limit)})
        except (TypeError, ValueError) as exc:
            return error_response(str(exc), 400)
        except LookupError as exc:
            return error_response(str(exc), 404)

    @app.get("/api/analytics/genres")
    def genre_analytics():
        frame = load_movie_frame()
        distribution, averages = genre_statistics(frame)
        return jsonify({"success": True, "data": {
            "distribution": records(distribution, ["genre", "movies"]),
            "average_rating": records(averages, ["genre", "average_rating", "rated_movies", "movies"]),
            "trends": genre_trends(frame),
        }})

    @app.get("/api/analytics/languages")
    def language_analytics():
        distribution, averages = language_statistics(load_movie_frame().drop_duplicates(subset=["id"]))
        return jsonify({"success": True, "data": {
            "distribution": records(distribution, ["language", "movies"]),
            "average_rating": records(averages, ["language", "average_rating", "rated_movies", "movies"]),
        }})

    @app.get("/api/analytics/years")
    def year_analytics():
        years, averages = year_statistics(load_movie_frame().drop_duplicates(subset=["id"]))
        return jsonify({"success": True, "data": {
            "movies_by_year": records(years, ["year", "count"]),
            "average_rating_by_year": records(averages, ["year", "average_rating", "rated_movies"]),
        }})

    return app


def error_response(message, status):
    return jsonify({"success": False, "error": {"message": message}}), status


def parse_integer(raw, name, minimum=None, maximum=None):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Query parameter '{name}' must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"Query parameter '{name}' must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"Query parameter '{name}' must be at most {maximum}.")
    return value


def parse_number(raw, name, minimum=None):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Query parameter '{name}' must be a number.")
    if minimum is not None and value < minimum:
        raise ValueError(f"Query parameter '{name}' must be at least {minimum}.")
    return value


def parse_movie_filters(args):
    page = parse_integer(args.get("page", 1), "page", minimum=1)
    limit = parse_integer(args.get("limit", 20), "limit", minimum=1, maximum=100)
    filters = {
        "page": page,
        "limit": limit,
        "search": args.get("search", args.get("q", "")).strip(),
        "genre": args.get("genre", "").strip(),
        "language": args.get("language", "").strip(),
        "certificate": args.get("certificate", "").strip(),
        "year": parse_integer(args["year"], "year", minimum=1) if args.get("year") else None,
        "year_from": parse_integer(args["year_from"], "year_from", minimum=1) if args.get("year_from") else None,
        "year_to": parse_integer(args["year_to"], "year_to", minimum=1) if args.get("year_to") else None,
        "min_rating": parse_number(args["min_rating"], "min_rating", minimum=0) if args.get("min_rating") else None,
        "max_rating": parse_number(args["max_rating"], "max_rating", minimum=0) if args.get("max_rating") else None,
        "min_votes": parse_integer(args["min_votes"], "min_votes", minimum=0) if args.get("min_votes") else None,
        "min_runtime": parse_integer(args["min_runtime"], "min_runtime", minimum=0) if args.get("min_runtime") else None,
        "max_runtime": parse_integer(args["max_runtime"], "max_runtime", minimum=0) if args.get("max_runtime") else None,
        "cast": args.get("cast", "").strip(),
        "director": args.get("director", "").strip(),
        "sort": args.get("sort", "rating_desc"),
    }
    if filters["min_rating"] is not None and filters["max_rating"] is not None and filters["min_rating"] > filters["max_rating"]:
        raise ValueError("'min_rating' cannot be greater than 'max_rating'.")
    if filters["year_from"] is not None and filters["year_to"] is not None and filters["year_from"] > filters["year_to"]:
        raise ValueError("'year_from' cannot be greater than 'year_to'.")
    if filters["min_runtime"] is not None and filters["max_runtime"] is not None and filters["min_runtime"] > filters["max_runtime"]:
        raise ValueError("'min_runtime' cannot be greater than 'max_runtime'.")
    if filters["sort"] not in SORTS:
        allowed = ", ".join(sorted(SORTS))
        raise ValueError(f"Invalid sort. Use one of: {allowed}.")
    return filters


def build_movie_query(filters):
    query = load_movies()
    if filters["search"]:
        query = query[query["title"].str.contains(filters["search"], case=False, na=False, regex=False)]
    if filters["genre"]:
        query = query[query["genres"].map(lambda genres: filters["genre"] in genres)]
    if filters["language"]:
        query = query[query["language"] == filters["language"]]
    if filters["year"] is not None:
        query = query[query["year"] == filters["year"]]
    if filters["year_from"] is not None:
        query = query[query["year"] >= filters["year_from"]]
    if filters["year_to"] is not None:
        query = query[query["year"] <= filters["year_to"]]
    if filters["min_rating"] is not None:
        query = query[query["rating"] >= filters["min_rating"]]
    if filters["max_rating"] is not None:
        query = query[query["rating"] <= filters["max_rating"]]
    if filters["min_votes"] is not None:
        query = query[query["votes"] >= filters["min_votes"]]
    if filters["min_runtime"] is not None:
        query = query[query["runtime"] >= filters["min_runtime"]]
    if filters["max_runtime"] is not None:
        query = query[query["runtime"] <= filters["max_runtime"]]
    if filters["cast"]:
        query = query[query["cast"].map(lambda cast: filters["cast"] in cast)]
    if filters["director"]:
        query = query[query["director"].str.contains(filters["director"], case=False, na=False, regex=False)]
    if filters["certificate"]:
        query = query[query["certificate"] == filters["certificate"]]
    sort_column, ascending = SORTS[filters["sort"]]
    return query.sort_values([sort_column, "id"], ascending=[ascending, True], na_position="last")


def paginated_response(query, page, limit):
    total = len(query)
    movies = query.iloc[(page - 1) * limit:page * limit]
    return jsonify({
        "success": True,
        "data": [movie_to_dict(movie) for _, movie in movies.iterrows()],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit if total else 0,
        },
    })


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
