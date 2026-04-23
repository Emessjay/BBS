"""
db.py  —  SQLite helpers for the CLI-based JBBS (bbs_db.py, migrate.py).

Exports:
    DB_FILE             — absolute path to bbs.db
    get_db()            — context manager that yields a live connection
    init_db()           — creates the users and posts tables
    validate_board()    — raises ValueError if a board name is unsafe
    hash_password()     — returns a salted PBKDF2 hash string
    verify_password()   — checks a plaintext password against a stored hash

SCHEMA
──────
  users
    id             INTEGER  primary key, auto-increment
    username       TEXT     unique, not null
    password_hash  TEXT     nullable (NULL for CLI-created users who
                            never registered a password)

  posts
    id        INTEGER  primary key, auto-increment
    user_id   INTEGER  foreign key → users.id, not null
    board     TEXT     not null, default 'general'
    message   TEXT     not null
    timestamp TEXT     ISO-8601, e.g. "2026-03-24T14:01:32"

WHY ONE posts TABLE INSTEAD OF ONE TABLE PER BOARD
───────────────────────────────────────────────────
An earlier version used a separate table per board (board_general,
board_tech, etc.).  That design had two real costs:

  1. "All posts by user X" turned into either N queries (one per
     board) or a mess of UNION ALL SQL.  With a single posts table
     it is a plain JOIN.

  2. "All boards" had to read sqlite_master to discover table names,
     then issue a COUNT query per table.  Now it is one GROUP BY.

The new design keeps the board identity right on the row.  There is
no separate `boards` table — a board exists exactly when at least
one post references it (queries use `SELECT DISTINCT board`).  That
matches the BBS user model: boards appear by being posted to.
"""

import re
import sqlite3
import os
import hmac
import hashlib
from contextlib import contextmanager

# bbs.db lives next to this script regardless of the working directory.
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bbs.db")

# Board names are user input, so we constrain them: letters, digits,
# and underscores only.  This no longer matters for SQL safety (board
# goes through ? placeholders now, not into table names), but it still
# prevents junk like empty strings or whitespace from becoming a
# board identity.  Length cap keeps runaway input from wedging the UI.
_BOARD_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_BOARD_MAX_LEN = 32


def validate_board(name: str) -> None:
    """
    Raise ValueError if `name` is not an acceptable board name.

    Called at the input boundary (before INSERT or WHERE-filter).
    Silent when the name is fine, so callers can treat it as an
    assertion-style guard.
    """
    if not isinstance(name, str) or not _BOARD_NAME_RE.match(name) or len(name) > _BOARD_MAX_LEN:
        raise ValueError(
            f"Invalid board name: {name!r} — use letters, digits, or "
            f"underscores only (max {_BOARD_MAX_LEN} chars)"
        )


# ──────────────────────────────────────────────────────────────────────
#  Password hashing (stdlib only — no bcrypt dependency)
# ──────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """
    Return a salted PBKDF2-SHA256 hash as  salt_hex:key_hex.

    Uses 16 random bytes of salt and 100 000 iterations — strong
    enough for JBBS while staying in the stdlib.
    """
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored: str) -> bool:
    """Check a plaintext password against a hash from hash_password()."""
    salt_hex, key_hex = stored.split(":")
    salt = bytes.fromhex(salt_hex)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    # hmac.compare_digest is a constant-time comparison — resists
    # timing attacks on the hash check.
    return hmac.compare_digest(key.hex(), key_hex)


# ──────────────────────────────────────────────────────────────────────
#  Connection & schema
# ──────────────────────────────────────────────────────────────────────

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
    Create the users and posts tables if they don't already exist.

    Safe to call on every startup — CREATE TABLE IF NOT EXISTS is
    idempotent.  Nothing fancy here: no ALTER TABLE migrations, no
    schema-version logic.  This is the ONE and ONLY schema the CLI
    has ever shipped.
    """
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL REFERENCES users(id),
                board     TEXT    NOT NULL DEFAULT 'general',
                message   TEXT    NOT NULL,
                timestamp TEXT    NOT NULL
            )
        """)
