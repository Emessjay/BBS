#!/usr/bin/env python3
"""
migrate.py  —  Part C: JSON → SQLite migration

Reads bbs.json (Part A format) and populates bbs.db (Part B schema).

Edge-case handling
──────────────────
  - If bbs.db already contains data, it is backed up to
    bbs_backup_<timestamp>.db.  Existing DB posts are merged with the
    incoming JSON posts, and everything is re-inserted in chronological
    order so that post IDs always increase with time (ID 1 = earliest).

  - A username→id dictionary is built incrementally as each user is first
    encountered during insertion.  cursor.lastrowid gives us the auto-
    generated id immediately — no follow-up SELECT needed.

  - Running migrate.py twice WILL duplicate the JSON posts (the first run's
    copies are read back from the DB and merged with the JSON originals).
    The backup exists so you can recover the previous state if this happens.

Usage:
    python migrate.py
"""

import sys
import os
import json
import shutil
from datetime import datetime

from db import DB_FILE, get_db, init_db

# ──────────────────────────────────────────────────────────────────────────────
#  Terminal colors  (same palette as bbs.py / bbs_db.py)
# ──────────────────────────────────────────────────────────────────────────────
LIME   = "\033[38;5;118m"
PURPLE = "\033[38;5;135m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# JSON source file (Part A storage)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE  = os.path.join(SCRIPT_DIR, "bbs.json")


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_json_posts() -> list[dict]:
    """Load posts from bbs.json.  Returns [] if the file doesn't exist."""
    if not os.path.exists(JSON_FILE):
        return []
    with open(JSON_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_db_posts() -> list[dict]:
    """
    Read every post out of the existing bbs.db as plain dicts.

    JOINs in the username so the returned dicts match bbs.json's format:
        {"username": ..., "message": ..., "timestamp": ...}

    Returns [] if the DB doesn't exist or the tables are missing/empty.
    """
    if not os.path.exists(DB_FILE):
        return []
    try:
        with get_db() as conn:
            rows = conn.execute("""
                SELECT u.username, p.message, p.timestamp
                  FROM posts p
                  JOIN users u ON p.user_id = u.id
            """).fetchall()
    except Exception:
        # DB exists but tables might not (e.g. brand-new file).
        return []

    return [
        {"username": r[0], "message": r[1], "timestamp": r[2]}
        for r in rows
    ]


def backup_db() -> str:
    """Copy bbs.db → bbs_backup_<timestamp>.db and return the backup path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(SCRIPT_DIR, f"bbs_backup_{ts}.db")
    shutil.copy2(DB_FILE, backup_path)
    return backup_path


def wipe_db() -> None:
    """
    Drop both tables and recreate them from scratch.

    Dropping resets the AUTOINCREMENT counters so the first INSERT gets id=1,
    which is the whole point — IDs will match chronological insertion order.
    Posts are dropped first because they hold a foreign key into users.
    """
    with get_db() as conn:
        conn.execute("DROP TABLE IF EXISTS posts")
        conn.execute("DROP TABLE IF EXISTS users")
    init_db()


# ──────────────────────────────────────────────────────────────────────────────
#  Main migration logic
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── 1. Load JSON posts ───────────────────────────────────────────────
    json_posts = load_json_posts()
    if not json_posts:
        print(f"  {PURPLE}Nothing to migrate:{RESET} bbs.json is empty or missing.")
        return

    print(f"  {DIM}Loaded {RESET}{LIME}{len(json_posts)}{RESET}{DIM} post(s) from bbs.json{RESET}")

    # ── 2. Pull existing DB posts out before we wipe ─────────────────────
    db_posts = load_db_posts()
    if db_posts:
        print(f"  {DIM}Found  {RESET}{LIME}{len(db_posts)}{RESET}{DIM} existing post(s) in bbs.db{RESET}")
        backup_path = backup_db()
        print(f"  {DIM}Backed up to {RESET}{PURPLE}{os.path.basename(backup_path)}{RESET}")

    # ── 3. Wipe and recreate tables (resets AUTOINCREMENT to 1) ──────────
    wipe_db()

    # ── 4. Merge all posts and sort by timestamp ─────────────────────────
    #    ISO-8601 timestamps ("2026-03-24T14:01:32") sort lexicographically
    #    in the same order as chronologically, so a plain string sort works.
    all_posts = json_posts + db_posts
    all_posts.sort(key=lambda p: p["timestamp"])

    # ── 5. Insert in chronological order ─────────────────────────────────
    #    Build a username → user_id dict as we go.  When a user appears for
    #    the first time we INSERT them and grab lastrowid; every subsequent
    #    post from that user reuses the cached id.  Zero SELECTs needed.
    user_dict: dict[str, int] = {}

    with get_db() as conn:
        for post in all_posts:
            username = post["username"]

            if username not in user_dict:
                cursor = conn.execute(
                    "INSERT INTO users (username) VALUES (?)",
                    (username,),
                )
                user_dict[username] = cursor.lastrowid

            conn.execute(
                "INSERT INTO posts (user_id, message, timestamp) VALUES (?, ?, ?)",
                (user_dict[username], post["message"], post["timestamp"]),
            )

    # ── Done ─────────────────────────────────────────────────────────────
    print(
        f"\n  {LIME}Migrated {len(all_posts)} post(s){RESET}"
        f" {DIM}from {len(user_dict)} user(s) into bbs.db{RESET}\n"
    )


if __name__ == "__main__":
    main()
