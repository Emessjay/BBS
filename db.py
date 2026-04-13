"""
db.py  —  SQLite connection helper for bbs_db.py

Exports:
    DB_FILE   — absolute path to bbs.db
    get_db()  — context manager that yields a live connection,
                commits on clean exit, rolls back on exception, always closes
    init_db() — creates the users and posts tables if they don't exist yet
                (safe to call on every startup — CREATE TABLE IF NOT EXISTS)

Schema
──────
  users
    id        INTEGER  primary key, auto-increment
    username  TEXT     unique, not null

  posts
    id        INTEGER  primary key, auto-increment
    user_id   INTEGER  foreign key → users.id, not null
    message   TEXT     not null
    timestamp TEXT     ISO-8601, e.g. "2026-03-24T14:01:32"

Why two tables instead of storing the username on every post?
  Normalisation: the username lives in exactly one place.  If you ever want to
  rename a user, you change one row — not every post they've written.
  It also lets us JOIN efficiently and enforce uniqueness at the DB level.
"""

import sqlite3
import os
from contextlib import contextmanager

# bbs.db lives next to this script regardless of the working directory.
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bbs.db")


@contextmanager
def get_db():
    """
    Context manager for a SQLite connection.

    Usage:
        with get_db() as conn:
            conn.execute("SELECT ...")

    On clean exit  → commits the transaction
    On exception   → rolls back, then re-raises
    Always         → closes the connection
    """
    conn = sqlite3.connect(DB_FILE)

    # Enforce foreign-key constraints (SQLite disables them by default).
    # Without this, inserting a post with a non-existent user_id would silently
    # succeed — which would corrupt the relationship between the two tables.
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """
    Create the users and posts tables if they don't already exist.

    IF NOT EXISTS makes this idempotent — safe to call every time the
    program starts without wiping existing data.
    """
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    NOT NULL UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL REFERENCES users(id),
                message   TEXT    NOT NULL,
                timestamp TEXT    NOT NULL
            )
        """)
