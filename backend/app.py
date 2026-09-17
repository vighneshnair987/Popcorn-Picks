"""Flask REST API for Popcorn Picks."""

import math
import os

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import asc, desc, func
from sqlalchemy.exc import SQLAlchemyError

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
from models import CastMember, Genre, Movie


SORTS = {
    "rating_desc": desc(Movie.rating),
    "rating_asc": asc(Movie.rating),
    "votes_desc": desc(Movie.votes),
    "votes_asc": asc(Movie.votes),
    "year_desc": desc(Movie.year),
    "year_asc": asc(Movie.year),
    "title_asc": asc(Movie.title),
    "title_desc": desc(Movie.title),
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
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("The movie database could not be queried.", 500)

    @app.get("/api/movies/<movie_id>")
    def get_movie(movie_id):
        try:
            parsed_id = parse_integer(movie_id, "id", minimum=1)
            movie = db.session.get(Movie, parsed_id)
            if movie is None:
                return error_response("Movie not found.", 404)
            return jsonify({"success": True, "data": movie.to_dict()})
        except ValueError as exc:
            return error_response(str(exc), 400)
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("The movie database could not be queried.", 500)

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
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("The movie database could not be queried.", 500)

    @app.get("/api/genres")
    def list_genres():
        try:
            names = db.session.query(Genre.name).order_by(Genre.name).all()
            return jsonify({"success": True, "data": [name for (name,) in names]})
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("The genre list could not be loaded.", 500)

    @app.get("/api/languages")
    def list_languages():
        try:
            languages = (
                db.session.query(Movie.language)
                .filter(Movie.language.isnot(None), Movie.language != "")
                .distinct()
                .order_by(Movie.language)
                .all()
            )
            return jsonify({"success": True, "data": [language for (language,) in languages]})
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("The language list could not be loaded.", 500)

    @app.get("/api/analytics")
    def analytics_overview():
        try:
            return jsonify({"success": True, "data": summary(load_movie_frame())})
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("Analytics could not be calculated.", 500)

    @app.get("/api/trending")
    def trending_movies():
        try:
            return jsonify({"success": True, "data": trending(load_movie_frame())})
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("Trending movies could not be calculated.", 500)

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
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("Recommendations could not be calculated.", 500)

    @app.get("/api/analytics/genres")
    def genre_analytics():
        try:
            frame = load_movie_frame()
            distribution, averages = genre_statistics(frame)
            return jsonify({"success": True, "data": {
                "distribution": records(distribution, ["genre", "movies"]),
                "average_rating": records(averages, ["genre", "average_rating", "rated_movies", "movies"]),
                "trends": genre_trends(frame),
            }})
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("Genre analytics could not be calculated.", 500)

    @app.get("/api/analytics/languages")
    def language_analytics():
        try:
            distribution, averages = language_statistics(load_movie_frame().drop_duplicates(subset=["id"]))
            return jsonify({"success": True, "data": {
                "distribution": records(distribution, ["language", "movies"]),
                "average_rating": records(averages, ["language", "average_rating", "rated_movies", "movies"]),
            }})
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("Language analytics could not be calculated.", 500)

    @app.get("/api/analytics/years")
    def year_analytics():
        try:
            years, averages = year_statistics(load_movie_frame().drop_duplicates(subset=["id"]))
            return jsonify({"success": True, "data": {
                "movies_by_year": records(years, ["year", "count"]),
                "average_rating_by_year": records(averages, ["year", "average_rating", "rated_movies"]),
            }})
        except SQLAlchemyError:
            db.session.rollback()
            return error_response("Year analytics could not be calculated.", 500)

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
    query = db.session.query(Movie)
    if filters["search"]:
        query = query.filter(Movie.title.ilike(f"%{filters['search']}%"))
    if filters["genre"]:
        query = query.filter(Movie.genres.any(Genre.name == filters["genre"]))
    if filters["language"]:
        query = query.filter(Movie.language == filters["language"])
    if filters["year"] is not None:
        query = query.filter(Movie.year == filters["year"])
    if filters["year_from"] is not None:
        query = query.filter(Movie.year >= filters["year_from"])
    if filters["year_to"] is not None:
        query = query.filter(Movie.year <= filters["year_to"])
    if filters["min_rating"] is not None:
        query = query.filter(Movie.rating >= filters["min_rating"])
    if filters["max_rating"] is not None:
        query = query.filter(Movie.rating <= filters["max_rating"])
    if filters["min_votes"] is not None:
        query = query.filter(Movie.votes >= filters["min_votes"])
    if filters["min_runtime"] is not None:
        query = query.filter(Movie.runtime >= filters["min_runtime"])
    if filters["max_runtime"] is not None:
        query = query.filter(Movie.runtime <= filters["max_runtime"])
    if filters["cast"]:
        query = query.filter(Movie.cast_members.any(CastMember.name == filters["cast"]))
    if filters["director"]:
        query = query.filter(Movie.director.ilike(filters["director"]))
    if filters["certificate"]:
        query = query.filter(Movie.certificate == filters["certificate"])
    return query.order_by(SORTS[filters["sort"]], Movie.id)


def paginated_response(query, page, limit):
    total = query.order_by(None).count()
    movies = query.offset((page - 1) * limit).limit(limit).all()
    return jsonify({
        "success": True,
        "data": [movie.to_dict() for movie in movies],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": math.ceil(total / limit) if total else 0,
        },
    })


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)