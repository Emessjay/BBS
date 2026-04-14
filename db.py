"""
db.py  —  SQLite connection helper for bbs_db.py

Exports:
    DB_FILE        — absolute path to bbs.db
    get_db()       — context manager that yields a live connection
    init_db()      — creates the users table and the default "general" board
    board_table()  — returns the sanitised table name for a board
    create_board() — creates a board table if it doesn't exist
    get_board_names() — lists all board names from sqlite_master
    hash_password()   — returns a salted PBKDF2 hash string
    verify_password() — checks a plaintext password against a stored hash

Schema
──────
  users
    id            INTEGER  primary key, auto-increment
    username      TEXT     unique, not null
    password_hash TEXT     nullable (NULL for legacy/CLI-created users)

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
import hmac
import hashlib
from contextlib import contextmanager

# bbs.db lives next to this script regardless of the working directory.
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bbs.db")

# Board names must be alphanumeric + underscores only.
# This is critical because table names can't use ? placeholders,
# so we validate the name before interpolating it into SQL.
_BOARD_NAME_RE = re.compile(r'^[a-zA-Z0-9_]+$')


def hash_password(password: str) -> str:
    """
    Return a salted PBKDF2-SHA256 hash as  salt_hex:key_hex.

    Uses 16 random bytes of salt and 100 000 iterations — strong enough
    for JBBS while staying in the stdlib (no bcrypt dependency).
    """
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored: str) -> bool:
    """Check a plaintext password against a hash produced by hash_password()."""
    salt_hex, key_hex = stored.split(":")
    salt = bytes.fromhex(salt_hex)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return hmac.compare_digest(key.hex(), key_hex)


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
        WHERE type = 'table' AND name LIKE 'board\\_%' ESCAPE '\\'
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
    If the users table already exists but lacks the password_hash column
    (pre-Gold schema), ALTER TABLE adds it non-destructively.
    """
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT
            )
        """)
        # Migrate older databases that lack the password_hash column.
        columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "password_hash" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")

        create_board(conn, "general")
