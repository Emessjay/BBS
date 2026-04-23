"""
db.py — SQLite helpers for the BBS webserver.

This module is intentionally small.  It owns three things:

    1. WHERE the database lives (one file path, picked from the
       BBS_DB_FILE env var with a sensible default).
    2. HOW to open a connection safely (a context manager that
       commits on success, rolls back on exception, and always
       closes).
    3. THE SCHEMA itself, created on first startup.

Everything else — request validation, status codes, response shapes —
belongs in main.py.  Keeping those concerns separate means the tests
can exercise the DB layer in isolation and the API layer can be
swapped for a different storage backend later without a rewrite.

A note on env-var-driven paths
──────────────────────────────
We read BBS_DB_FILE inside `_db_path()` on every call, NOT at
import time.  Why?  Because pytest's monkeypatch.setenv() runs
AFTER the test collection imports this module.  If we captured the
path at import time, every test would end up pointing at the same
default file no matter what the fixture said.
"""

import os
import sqlite3
from contextlib import contextmanager


def _db_path() -> str:
    """
    Where the SQLite file lives for THIS process.

    Priority:
      1. $BBS_DB_FILE if set (tests use this)
      2. bbs.db next to this module (the default for `uvicorn main:app`)

    Read on every connection so tests can swap it mid-process.
    """
    env = os.environ.get("BBS_DB_FILE")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "bbs.db")


@contextmanager
def get_db():
    """
    Yield a live sqlite3.Connection with sane defaults.

    Guarantees, in order of importance:
      - foreign_keys = ON  so `REFERENCES users(id)` actually enforces.
      - row_factory = Row  so rows are accessible by column name
                           (`row["username"]`) instead of by index.
      - commit-on-success / rollback-on-exception semantics, courtesy
        of the try/except/finally dance below.
      - the connection is ALWAYS closed, even if the caller raises,
        thanks to the finally clause.

    Usage:
        with get_db() as conn:
            conn.execute("INSERT INTO users ...", (...,))
            # exit the `with` normally → commit
            # raise inside the `with`   → rollback
    """
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
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
    Create tables if they don't exist.

    SCHEMA (gold)
    ─────────────
      users
        id          INTEGER  primary key, auto-increment
        username    TEXT     unique, not null
        created_at  TEXT     ISO-8601 "YYYY-MM-DDTHH:MM:SS"
        bio         TEXT     nullable (silver)

      posts
        id          INTEGER  primary key, auto-increment
        user_id     INTEGER  foreign key → users.id, not null
        message     TEXT     not null
        created_at  TEXT     ISO-8601 "YYYY-MM-DDTHH:MM:SS"
        updated_at  TEXT     nullable; set by PATCH /posts/{id} (silver)
        board       TEXT     not null, default 'general' (gold)

      post_count is NOT a column — it is computed per-request from
      a correlated subquery `SELECT COUNT(*) FROM posts WHERE user_id = u.id`.
      Storing it would require keeping a counter in sync on every
      post INSERT/DELETE, and drift bugs are common.  Compute it.

      Boards are likewise NOT a separate table.  A board exists when
      at least one post references it; GET /boards is a
      `SELECT board, COUNT(*) FROM posts GROUP BY board`.  No way to
      reserve an empty board — if that's ever needed, add a `boards`
      table later and backfill from the distinct set of post boards.

    DIFFERENCES FROM A1 (after the refactor)
    ────────────────────────────────────────
      - A1 carries a password_hash column on users and supports
        register/login commands.  A2's API has no concept of
        passwords — X-Username is explicitly not authentication —
        so that column is gone.
      - A1 uses `timestamp` on posts; A2 renames it to `created_at`
        to match the JSON field name the spec mandates in responses.
        A2 also gains an `updated_at` column (silver, nullable) for
        PATCH and loses nothing.
      - A1's insert-post path runs INSERT OR IGNORE on users, so
        posting as an unknown name silently creates the account.
        A2 drops that behaviour: POST /posts with an unknown
        X-Username returns 404.  Enforcement lives in main.py,
        not here — the DB layer has no opinion on status codes.
      - Both files now use a flat `posts` table with a `board`
        column (the old table-per-board design in A1 was reworked in
        an earlier review round).  A2 adds indexes on
        posts.user_id and posts.board; A1 does not, because its CLI
        doesn't have the hot read paths (correlated subquery per
        user, GROUP BY on every boards list) that the webserver does.

    CREATE TABLE IF NOT EXISTS (and CREATE INDEX IF NOT EXISTS) are
    idempotent, so it is safe to call init_db() on every startup.
    """
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT    NOT NULL UNIQUE,
                created_at  TEXT    NOT NULL,
                bio         TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                message     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT,
                board       TEXT    NOT NULL DEFAULT 'general'
            )
        """)

        # Indexes.  SQLite does NOT auto-index foreign-key columns,
        # so without these two the correlated subquery used for
        # UserOut.post_count (scans posts once per user) and the
        # GET /posts?board= / GET /boards queries (scan posts once
        # per request) degrade to O(N) table scans.  These indexes
        # turn both into index lookups.  IF NOT EXISTS makes it safe
        # to call init_db() on every startup.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_user_id ON posts (user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_board ON posts (board)"
        )
