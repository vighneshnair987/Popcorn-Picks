"""
Database configuration and session management for Popcorn Picks.

Provides a configured SQLAlchemy instance and helper utilities for
creating / resetting the database.
"""

import os
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Default path: backend/popcorn_picks.db  (sibling of this file)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "popcorn_picks.db")


def get_database_uri(db_path: str | None = None) -> str:
    """Return an SQLite connection URI."""
    path = db_path or DEFAULT_DB_PATH
    return f"sqlite:///{path}"


def init_db(app):
    """Create all tables that don't yet exist."""
    with app.app_context():
        # Import models so SQLAlchemy sees them before create_all()
        import models  # noqa: F401
        db.create_all()


def reset_db(app):
    """Drop every table and recreate the schema (destructive)."""
    with app.app_context():
        import models  # noqa: F401
        db.drop_all()
        db.create_all()
