"""
SQLAlchemy models for Popcorn Picks.

Schema overview
───────────────
Movie           — core table with scalar fields
Genre           — lookup table of unique genre names
CastMember      — lookup table of unique actor / cast names
movie_genres    — many-to-many: Movie ↔ Genre
movie_cast      — many-to-many: Movie ↔ CastMember (with display order)

Design decisions
────────────────
• genres and cast are normalised into their own tables so that the
  backend can filter, count and aggregate by genre or actor with
  simple JOINs rather than parsing delimited strings.
• language, director, and certificate remain as columns on Movie
  because the frontend treats each as a single value per movie.
  They are indexed for fast filtering.
• movie_cast carries a `position` column to preserve the ordering
  shown in the frontend's "Top cast" list.
"""

from database import db


# ── Association tables ─────────────────────────────────────────────

movie_genres = db.Table(
    "movie_genres",
    db.Column(
        "movie_id",
        db.Integer,
        db.ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "genre_id",
        db.Integer,
        db.ForeignKey("genres.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

movie_cast = db.Table(
    "movie_cast",
    db.Column(
        "movie_id",
        db.Integer,
        db.ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "cast_member_id",
        db.Integer,
        db.ForeignKey("cast_members.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "position",
        db.Integer,
        nullable=False,
        default=0,
        doc="Display order (0 = first billed).",
    ),
)


# ── Genre ──────────────────────────────────────────────────────────

class Genre(db.Model):
    """A unique genre label (e.g. Action, Drama, Thriller)."""

    __tablename__ = "genres"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(60), unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<Genre {self.name!r}>"


# ── CastMember ─────────────────────────────────────────────────────

class CastMember(db.Model):
    """A unique actor / cast member name."""

    __tablename__ = "cast_members"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(200), unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<CastMember {self.name!r}>"


# ── Movie ──────────────────────────────────────────────────────────

class Movie(db.Model):
    """
    Central movie record.

    Scalar fields live directly on this table.  Genres and cast are
    linked via many-to-many association tables.
    """

    __tablename__ = "movies"

    # Primary key
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Core metadata
    title = db.Column(db.String(300), nullable=False, index=True)
    imdb_id = db.Column(db.String(20), nullable=False, unique=True, index=True)
    year = db.Column(db.Integer, nullable=True, index=True)
    rating = db.Column(db.Float, nullable=True, index=True)
    votes = db.Column(db.Integer, nullable=True, index=True)
    language = db.Column(db.String(60), nullable=True, index=True)
    runtime = db.Column(db.Integer, nullable=True)   # minutes
    certificate = db.Column(db.String(20), nullable=True, index=True)
    director = db.Column(db.String(200), nullable=True, index=True)
    description = db.Column(db.Text, nullable=True)

    # Relationships
    genres = db.relationship(
        "Genre",
        secondary=movie_genres,
        backref=db.backref("movies", lazy="dynamic"),
        lazy="joined",           # eager-load genres with the movie
        order_by=Genre.name,
    )

    cast_members = db.relationship(
        "CastMember",
        secondary=movie_cast,
        backref=db.backref("movies", lazy="dynamic"),
        lazy="joined",           # eager-load cast with the movie
        order_by=movie_cast.c.position,
    )

    # ── Serialisation ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Return a plain dict matching the frontend's expected schema:

            { id, title, year, rating, votes, genres, language,
              runtime, certificate, director, cast, description }

        `genres` → list of genre name strings
        `cast`   → list of actor name strings (ordered by position)
        """
        return {
            "id": self.id,
            "title": self.title,
            "year": self.year,
            "rating": self.rating,
            "votes": self.votes,
            "genres": [g.name for g in self.genres],
            "language": self.language,
            "runtime": self.runtime,
            "certificate": self.certificate,
            "director": self.director,
            "cast": [c.name for c in self.cast_members],
            "description": self.description,
        }

    def __repr__(self):
        return f"<Movie {self.id} {self.title!r} ({self.year})>"
