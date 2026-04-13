#!/usr/bin/env python3
"""
bbs_db.py  —  Part B: SQLite-backed Bulletin Board System

Commands:
    python bbs_db.py post <username> <message>   post a new message
    python bbs_db.py read                         read all posts chronologically
    python bbs_db.py users                        list users who have posted
    python bbs_db.py search <keyword>             search messages (case-insensitive)

Data is stored in bbs.db (SQLite).  Schema is managed by db.py.
No third-party dependencies — uses only Python stdlib (sqlite3).

SQL INJECTION NOTE
──────────────────
Every query in this file uses ? placeholders.  User input is ALWAYS passed as
a parameter tuple — never interpolated into the SQL string with f-strings or
% formatting.  The sqlite3 driver escapes all parameter values before they
reach the database engine, making injection impossible.
"""

import sys
import os
from datetime import datetime

from db import get_db, init_db

# ──────────────────────────────────────────────────────────────────────────────
#  Terminal color constants  (ANSI 256-color escape codes)
#  Identical palette to bbs.py so both versions feel like the same system.
# ──────────────────────────────────────────────────────────────────────────────
LIME   = "\033[38;5;118m"
PURPLE = "\033[38;5;135m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# ──────────────────────────────────────────────────────────────────────────────
#  Splash banner  (only shown when bbs_db.py is run with no arguments)
#
#  Same box geometry as bbs.py: 2-indent + ║ + 44-inner + ║ = 48 visible chars.
#  Label updated to "SQLITE v1.0" to distinguish from the JSON version.
# ──────────────────────────────────────────────────────────────────────────────
BANNER = (
    "\n"
    f"  {PURPLE}╔{'═' * 44}╗{RESET}\n"
    f"  {PURPLE}║  {LIME}██████╗ ██████╗ ███████╗{PURPLE}                  ║{RESET}\n"
    f"  {PURPLE}║  {LIME}██╔══██╗██╔══██╗██╔════╝{PURPLE}                  ║{RESET}\n"
    f"  {PURPLE}║  {LIME}██████╔╝██████╔╝███████╗{PURPLE}                  ║{RESET}\n"
    f"  {PURPLE}║  {LIME}██╔══██╗██╔══██╗╚════██║{PURPLE}                  ║{RESET}\n"
    f"  {PURPLE}║  {LIME}██████╔╝██████╔╝███████║{PURPLE}                  ║{RESET}\n"
    f"  {PURPLE}║  {LIME}╚═════╝ ╚═════╝ ╚══════╝{PURPLE}                  ║{RESET}\n"
    f"  {PURPLE}║{'':44}║{RESET}\n"
    f"  {PURPLE}║  {LIME}BULLETIN BOARD SYSTEM{PURPLE}  {DIM}//{RESET}  {WHITE}SQLITE v1.0{RESET}    {PURPLE}║{RESET}\n"
    f"  {PURPLE}║{'':44}║{RESET}\n"
    f"  {PURPLE}╚{'═' * 44}╝{RESET}\n"
)


# ──────────────────────────────────────────────────────────────────────────────
#  Display helpers
# ──────────────────────────────────────────────────────────────────────────────

def format_post(username: str, message: str, timestamp: str) -> str:
    """
    Render a single post as a colored terminal line.

    Takes three plain strings rather than a dict, matching how sqlite3
    returns rows (by position).  Output format mirrors bbs.py exactly so
    that migrated data looks identical in both versions.
    """
    ts = timestamp[:16].replace("T", " ")   # "2026-03-24T14:01:32" → "2026-03-24 14:01"
    return (
        f"  {DIM}[{RESET}{PURPLE}{ts}{RESET}{DIM}]{RESET} "
        f"{LIME}{username}{RESET}"
        f"{DIM}:{RESET} "
        f"{WHITE}{message}{RESET}"
    )


def print_help() -> None:
    """Display the splash banner followed by a compact command reference."""
    print(BANNER)
    print(
        f"  {PURPLE}Commands:{RESET}\n"
        f"    {LIME}post{RESET}   {WHITE}<username> <message>{RESET}   post a new message\n"
        f"    {LIME}read{RESET}                           read all posts\n"
        f"    {LIME}users{RESET}                          list all users\n"
        f"    {LIME}search{RESET} {WHITE}<keyword>{RESET}              search posts (case-insensitive)\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Commands
# ──────────────────────────────────────────────────────────────────────────────

def cmd_post(username: str, message: str) -> None:
    """
    Add a post to the database, creating the user row first if needed.

    Two-step write, both inside the same transaction:
      1. INSERT OR IGNORE into users — no-op if the username already exists,
         which means repeat posters don't cause an error.
      2. Look up the user's integer id, then INSERT the post with that id as
         the foreign key.

    WHY NOT JUST STORE THE USERNAME ON THE POST?
    Normalisation: usernames live in one place (users.username).  Changing a
    username later means updating one row, not scanning every post they wrote.
    The JOIN in read/search is cheap; the structural benefit is permanent.

    INJECTION SAFETY: all three queries use ? placeholders.
    User-supplied strings (username, message) are never interpolated into SQL.
    """
    with get_db() as conn:
        # Create the user if this is their first post.
        conn.execute(
            "INSERT OR IGNORE INTO users (username) VALUES (?)",
            (username,),
        )

        # Retrieve the user's id (works whether the row was just created or
        # already existed — INSERT OR IGNORE guarantees the row is present).
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        user_id = row[0]

        # Insert the post with the resolved foreign key.
        conn.execute(
            "INSERT INTO posts (user_id, message, timestamp) VALUES (?, ?, ?)",
            (user_id, message, datetime.now().isoformat(timespec="seconds")),
        )

    print(f"  {LIME}Posted.{RESET}")


def cmd_read() -> None:
    """
    Print every post in chronological order (oldest first).

    The JOIN replaces the username lookup that Part A handles by storing the
    username directly on the post object.  ORDER BY p.id is equivalent to
    ordering by insertion time because AUTOINCREMENT ids are monotonically
    increasing.
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.username, p.message, p.timestamp
              FROM posts p
              JOIN users u ON p.user_id = u.id
             ORDER BY p.id ASC
        """).fetchall()

    if not rows:
        print(f"\n  {DIM}No posts yet. Be the first to transmit.{RESET}\n")
        return

    print()
    for username, message, timestamp in rows:
        print(format_post(username, message, timestamp))
    print()


def cmd_users() -> None:
    """
    Print each user who has posted, ordered by their first post.

    MIN(p.id) gives us the earliest post by each user, so ORDER BY MIN(p.id)
    preserves the same first-appearance order as the JSON version.
    The HAVING clause is defensive — it filters out users with no posts,
    though in practice the INSERT OR IGNORE in cmd_post means every user
    in the users table has at least one post.
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.username
              FROM users u
              JOIN posts p ON u.id = p.user_id
             GROUP BY u.id
            HAVING COUNT(p.id) > 0
             ORDER BY MIN(p.id) ASC
        """).fetchall()

    if not rows:
        print(f"\n  {DIM}No users yet.{RESET}\n")
        return

    print()
    for (username,) in rows:
        print(f"  {LIME}{username}{RESET}")
    print()


def cmd_search(keyword: str) -> None:
    """
    Print all posts whose message contains the keyword (case-insensitive).

    SQL version vs. JSON version
    ────────────────────────────
    Part A loads the entire bbs.json file into memory and loops through every
    post in Python — O(n) work done in the application layer.

    Here, a single SQL query does the filtering inside the database engine:

        WHERE p.message LIKE ?   with parameter "%keyword%"

    SQLite's LIKE is case-insensitive for ASCII characters by default, so
    this matches the behaviour of .lower() in Part A.

    INJECTION SAFETY
    ────────────────
    We build the LIKE pattern in Python  →  f"%{keyword}%"
    then pass it as a ? parameter.  This is NOT injection: the Python string
    concat happens before the query is sent; the driver then escapes the
    entire pattern value.  What we explicitly never do is:

        f"WHERE message LIKE '%{keyword}%'"   ← WRONG — interpolation into SQL

    Note: if the user's keyword itself contains % or _, those characters will
    act as LIKE wildcards (matching any substring / any single character).
    This is a reasonable power-user feature for a BBS search.
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.username, p.message, p.timestamp
              FROM posts p
              JOIN users u ON p.user_id = u.id
             WHERE p.message LIKE ?
             ORDER BY p.id ASC
        """, (f"%{keyword}%",)).fetchall()

    if not rows:
        print(f"\n  {DIM}No posts match {RESET}{PURPLE}'{keyword}'{RESET}{DIM}.{RESET}\n")
        return

    print()
    for username, message, timestamp in rows:
        print(format_post(username, message, timestamp))
    print()


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Parse sys.argv and dispatch to the appropriate command.

    init_db() is called unconditionally on every run.  It uses
    CREATE TABLE IF NOT EXISTS so it's a no-op after the first run —
    no risk of dropping data.
    """
    init_db()

    if len(sys.argv) < 2:
        print_help()
        return

    cmd = sys.argv[1].lower()

    if cmd == "post":
        if len(sys.argv) < 4:
            print(
                f"  {PURPLE}Usage:{RESET} python bbs_db.py post "
                f"{WHITE}<username> <message>{RESET}",
                file=sys.stderr,
            )
            sys.exit(1)
        username = sys.argv[2]
        message  = " ".join(sys.argv[3:])   # quoting optional, same as bbs.py
        cmd_post(username, message)

    elif cmd == "read":
        cmd_read()

    elif cmd == "users":
        cmd_users()

    elif cmd == "search":
        if len(sys.argv) < 3:
            print(
                f"  {PURPLE}Usage:{RESET} python bbs_db.py search {WHITE}<keyword>{RESET}",
                file=sys.stderr,
            )
            sys.exit(1)
        keyword = " ".join(sys.argv[2:])
        cmd_search(keyword)

    else:
        print(
            f"\n  {PURPLE}Unknown command:{RESET} {WHITE}{cmd}{RESET}\n"
            f"  Run {LIME}python bbs_db.py{RESET} with no arguments for help.\n",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
