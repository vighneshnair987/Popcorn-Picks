"""
Popcorn Picks — Database initialisation script.

Usage
─────
    python init_db.py              # create tables (safe — won't drop existing)
    python init_db.py --reset      # drop + recreate all tables (destructive)
    python init_db.py --verify     # create if needed, then print schema info

This script creates a minimal Flask app context purely for database
operations.  It does NOT start a web server.
"""

import argparse
import os
import sys

from flask import Flask
from sqlalchemy import inspect, text

from database import db, get_database_uri, DEFAULT_DB_PATH


def create_app(db_path: str | None = None) -> Flask:
    """Build a lightweight Flask app wired to the SQLite database."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri(db_path)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def init_tables(app: Flask) -> None:
    """Create all tables that don't already exist."""
    with app.app_context():
        # Importing models registers them with SQLAlchemy's metadata.
        import models  # noqa: F401
        db.create_all()
        print("[OK] Tables created / verified.")


def reset_tables(app: Flask) -> None:
    """Drop everything and recreate (destructive)."""
    with app.app_context():
        import models  # noqa: F401
        db.drop_all()
        db.create_all()
        print("[OK] All tables dropped and recreated.")


def verify_database(app: Flask) -> None:
    """Print every table and its columns to confirm the schema."""
    with app.app_context():
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()

        if not tables:
            print("[WARN] No tables found in the database.")
            return

        print(f"\n{'=' * 60}")
        print(f"  DATABASE: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print(f"  TABLES  : {len(tables)}")
        print(f"{'=' * 60}\n")

        for table_name in sorted(tables):
            columns = inspector.get_columns(table_name)
            pk_cols = inspector.get_pk_constraint(table_name).get("constrained_columns", [])
            fks = inspector.get_foreign_keys(table_name)
            indexes = inspector.get_indexes(table_name)

            print(f"  +-- {table_name}")
            for col in columns:
                pk_flag = " PK" if col["name"] in pk_cols else ""
                nullable = "" if col.get("nullable", True) else " NOT NULL"
                col_type = str(col["type"])
                print(f"  |   {col['name']:<20s} {col_type:<15s}{nullable}{pk_flag}")

            if fks:
                for fk in fks:
                    src = ", ".join(fk["constrained_columns"])
                    dst_table = fk["referred_table"]
                    dst_cols = ", ".join(fk["referred_columns"])
                    print(f"  |   FK: {src} -> {dst_table}({dst_cols})")

            if indexes:
                for idx in indexes:
                    cols = ", ".join(idx["column_names"])
                    unique = " UNIQUE" if idx.get("unique") else ""
                    print(f"  |   IX: {idx['name']} ({cols}){unique}")

            print(f"  +{'-' * 40}\n")

        # Quick connection test
        with db.engine.connect() as conn:
            result = conn.execute(text("SELECT sqlite_version()"))
            version = result.scalar()
            print(f"  SQLite version : {version}")

        print(f"  DB file exists : {os.path.exists(DEFAULT_DB_PATH)}")
        if os.path.exists(DEFAULT_DB_PATH):
            size = os.path.getsize(DEFAULT_DB_PATH)
            print(f"  DB file size   : {size:,} bytes")
        print()


def main():
    parser = argparse.ArgumentParser(description="Popcorn Picks — DB init")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables (destructive).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Print full schema information after creating tables.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Custom path for the SQLite file.",
    )
    args = parser.parse_args()

    app = create_app(args.db_path)

    if args.reset:
        reset_tables(app)
    else:
        init_tables(app)

    if args.verify:
        verify_database(app)
    else:
        # Always show a quick confirmation
        verify_database(app)


if __name__ == "__main__":
    main()
