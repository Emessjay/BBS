"""
db.py  —  SQLite connection helper for bbs_db.py

Exports:
    DB_FILE        — absolute path to bbs.db
    get_db()       — context manager that yields a live connection
    init_db()      — creates the users table and the default "general" board
    board_table()  — returns the sanitised table name for a board
    create_board() — creates a board table if it doesn't exist
    get_board_names() — lists all board names from sqlite_master

Schema
──────
  users
    id        INTEGER  primary key, auto-increment
    username  TEXT     unique, not null

  board_<name>   (one table per board, e.g. board_general, board_tech)
    id        INTEGER  primary key, auto-increment
    user_id   INTEGER  foreign key → users.id, not null
    message   TEXT     not null
    timestamp TEXT     ISO-8601, e.g. "2026-03-24T14:01:32"

Each board gets its own table so reading a single board never touches
rows from other boards — no filtering required, just a direct table scan.
"""

import re
import sqlite3
import os
from contextlib import contextmanager

# bbs.db lives next to this script regardless of the working directory.
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bbs.db")

# Board names must be alphanumeric + underscores only.
# This is critical because table names can't use ? placeholders,
# so we validate the name before interpolating it into SQL.
_BOARD_NAME_RE = re.compile(r'^[a-zA-Z0-9_]+$')


def board_table(name: str) -> str:
    """
    Return the table name for a board (e.g. "tech" → "board_tech").

    Raises ValueError if the name contains unsafe characters.
    """
    if not _BOARD_NAME_RE.match(name):
        raise ValueError(
            f"Invalid board name: {name!r} — only letters, digits, and underscores allowed"
        )
    return f"board_{name}"


def create_board(conn, name: str) -> None:
    """Create a board's table if it doesn't already exist."""
    table = board_table(name)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL REFERENCES users(id),
            message   TEXT    NOT NULL,
            timestamp TEXT    NOT NULL
        )
    """)


def get_board_names(conn) -> list[str]:
    """Return all board names by inspecting sqlite_master."""
    rows = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name LIKE 'board_%'
        ORDER BY name
    """).fetchall()
    return [row[0][6:] for row in rows]   # strip "board_" prefix


@contextmanager
def get_db():
    """
    Context manager for a SQLite connection.

    On clean exit  → commits the transaction
    On exception   → rolls back, then re-raises
    Always         → closes the connection
    """
    conn = sqlite3.connect(DB_FILE)
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
    Create the users table and the default "general" board table.

    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    NOT NULL UNIQUE
            )
        """)
        create_board(conn, "general")
