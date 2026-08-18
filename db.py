"""
db.py — small PostgreSQL connection helper built on psycopg2.

We avoid an ORM here to keep the codebase easy to read end-to-end for a
class project / portfolio piece. Every query goes through get_db() which
returns a connection with DictCursor so rows behave like dictionaries
(row["email"] instead of row[2]).
"""

import psycopg2
import psycopg2.extras
from flask import current_app, g


def get_db():
    """Open a new DB connection if one doesn't already exist for this request."""
    if "db" not in g:
        g.db = psycopg2.connect(
            host=current_app.config["POSTGRES_HOST"],
            user=current_app.config["POSTGRES_USER"],
            password=current_app.config["POSTGRES_PASSWORD"],
            dbname=current_app.config["POSTGRES_DB"],
            port=current_app.config["POSTGRES_PORT"],
            cursor_factory=psycopg2.extras.DictCursor  # <--- ADD THIS HERE!
        )
        g.db.autocommit = True
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_one(sql, params=None):
    """Run a SELECT and return a single row (dict) or None."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return dict(row) if row else None


def query_all(sql, params=None):
    """Run a SELECT and return all rows (list of dicts)."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        return [dict(row) for row in rows]


def execute(sql, params=None):
    """Run an INSERT / UPDATE / DELETE. Returns the new row's ID if RETURNING id is used."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params or ())
        try:
            row = cur.fetchone()
            if row:
                return row[0]
        except psycopg2.ProgrammingError:
            # no results to fetch
            pass
        return None


def init_app(app):
    app.teardown_appcontext(close_db)
