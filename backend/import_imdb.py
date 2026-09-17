"""Import the supplied IMDb archive into the Popcorn Picks database."""

import argparse
import csv
import io
import os
import zipfile
from collections import defaultdict

from flask import Flask
from sqlalchemy import func

from database import DEFAULT_DB_PATH, db, get_database_uri
from models import CastMember, Genre, Movie, movie_cast


ARCHIVE_DEFAULT = os.path.join(os.path.dirname(__file__), "archive.zip")
INDIA_LANGUAGES = {"as", "bn", "gu", "hi", "kn", "ml", "mr", "or", "pa", "ta", "te", "ur"}
CAST_CATEGORIES = {"actor", "actress"}


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri(db_path)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def csv_rows(archive, member):
    raw = archive.open(member)
    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    return text, csv.DictReader(text)


def value(raw):
    return None if raw in (None, "", "\\N") else raw


def integer(raw):
    raw = value(raw)
    return None if raw is None else int(float(raw))


def load_source_data(archive):
    text, rows = csv_rows(archive, "imdb_datasets/title.basics.csv")
    titles = {}
    for row in rows:
        if row["titleType"] == "movie":
            titles[row["tconst"]] = row
    text.close()

    ratings = {}
    text, rows = csv_rows(archive, "imdb_datasets/title.ratings.csv")
    for row in rows:
        if row["tconst"] in titles:
            ratings[row["tconst"]] = row
    text.close()

    directors = {}
    text, rows = csv_rows(archive, "imdb_datasets/title.crew.csv")
    for row in rows:
        if row["tconst"] in titles:
            directors[row["tconst"]] = value(row["directors"])
    text.close()

    languages = defaultdict(list)
    text, rows = csv_rows(archive, "imdb_datasets/title.akas.csv")
    for row in rows:
        if row["titleId"] in titles:
            language = value(row["language"])
            region = value(row["region"])
            if language:
                priority = 0 if region == "IN" or language in INDIA_LANGUAGES else 1
                languages[row["titleId"]].append((priority, int(row["ordering"]), language))
    text.close()

    principals = defaultdict(list)
    needed_names = set()
    for director_ids in directors.values():
        if director_ids:
            needed_names.update(director_ids.split(","))
    text, rows = csv_rows(archive, "imdb_datasets/title.principals.csv")
    for row in rows:
        if row["tconst"] in titles and row["category"] in CAST_CATEGORIES:
            principals[row["tconst"]].append((int(row["ordering"]), row["nconst"]))
            needed_names.add(row["nconst"])
    text.close()

    names = {}
    text, rows = csv_rows(archive, "imdb_datasets/name.basics.csv")
    for row in rows:
        if row["nconst"] in needed_names and value(row["primaryName"]):
            names[row["nconst"]] = row["primaryName"]
    text.close()
    return titles, ratings, directors, languages, principals, names


def import_movies(archive_path, db_path=None):
    app = create_app(db_path)
    with app.app_context(), zipfile.ZipFile(archive_path) as archive:
        existing = db.session.query(func.count(Movie.id)).scalar()
        if existing:
            raise RuntimeError(f"Database already contains {existing} movies; refusing to duplicate-import.")
        db.drop_all()
        db.create_all()

        titles, ratings, directors, languages, principals, names = load_source_data(archive)
        genres_by_name = {}
        cast_by_name = {}
        movies = []
        movie_genres = []
        movie_cast_rows = []

        for imdb_id, source in titles.items():
            rating = ratings.get(imdb_id)
            movie = Movie(
                imdb_id=imdb_id,
                title=source["primaryTitle"],
                year=integer(source["startYear"]),
                rating=float(rating["averageRating"]) if rating else None,
                votes=int(rating["numVotes"]) if rating else None,
                language=sorted(languages.get(imdb_id, [(2, 0, None)]))[0][2],
                runtime=integer(source["runtimeMinutes"]),
                description=None,
            )
            director_ids = value(directors.get(imdb_id))
            if director_ids:
                movie.director = ", ".join(names.get(person_id, person_id) for person_id in director_ids.split(","))
            movies.append(movie)

            for genre_name in (value(source["genres"]) or "").split(","):
                if genre_name:
                    genre = genres_by_name.setdefault(genre_name, Genre(name=genre_name))
                    movie_genres.append((movie, genre))
            for position, person_id in sorted(principals.get(imdb_id, [])):
                cast_name = names.get(person_id)
                if cast_name:
                    cast = cast_by_name.setdefault(cast_name, CastMember(name=cast_name))
                    movie_cast_rows.append((movie, cast, position))

        db.session.add_all(movies)
        db.session.add_all(genres_by_name.values())
        db.session.add_all(cast_by_name.values())
        db.session.flush()
        for movie, genre in movie_genres:
            movie.genres.append(genre)
        for movie, cast, position in movie_cast_rows:
            db.session.execute(movie_cast.insert().values(movie_id=movie.id, cast_member_id=cast.id, position=position))
        db.session.commit()
        print({"movies_imported": len(movies), "genres_imported": len(genres_by_name), "cast_members_imported": len(cast_by_name)})


def main():
    parser = argparse.ArgumentParser(description="Import supplied IMDb movie data.")
    parser.add_argument("--archive", default=ARCHIVE_DEFAULT)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()
    import_movies(args.archive, args.db_path)


if __name__ == "__main__":
    main()