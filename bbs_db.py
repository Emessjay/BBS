#!/usr/bin/env python3
"""
bbs_db.py  —  SQLite-backed Jack's Bulletin Board System (Gold: interactive sessions)

One-shot commands:
    python bbs_db.py post <username> <message>               post to "general"
    python bbs_db.py post <username> <board> <message>       post to a board
    python bbs_db.py read                                     read all posts
    python bbs_db.py read <board>                             read one board
    python bbs_db.py boards                                   list all boards
    python bbs_db.py users                                    list all users
    python bbs_db.py search <keyword>                         search posts

Interactive mode (Gold):
    python bbs_db.py register                                 create an account
    python bbs_db.py login                                    log in → jbbs> session

Data is stored in bbs.db (SQLite).  Schema is managed by db.py.

SQL INJECTION NOTE
──────────────────
Every query in this file uses ? placeholders.  User input — including
board names — is ALWAYS passed as a parameter tuple, never interpolated
into the SQL string with f-strings or % formatting.  The sqlite3 driver
escapes all parameter values before they reach the database engine,
making injection impossible.
"""

import sys
import getpass
import shlex
from datetime import datetime

from db import (
    get_db, init_db, validate_board,
    hash_password, verify_password,
)
from bbs_ui import LIME, PURPLE, WHITE, DIM, RESET, make_banner, format_post


# Splash banner tagged for the SQLite edition.  Built once at import
# time because the label never changes for the life of the process.
BANNER = make_banner("SQLITE v2.0")


# ──────────────────────────────────────────────────────────────────────
#  Help screens
# ──────────────────────────────────────────────────────────────────────

def print_help() -> None:
    """Display the splash banner followed by a compact command reference."""
    print(BANNER)
    print(
        f"  {PURPLE}One-shot commands:{RESET}\n"
        f"    {LIME}post{RESET}     {WHITE}<user> <message>{RESET}         post to general board\n"
        f"    {LIME}post{RESET}     {WHITE}<user> <board> <message>{RESET} post to a specific board\n"
        f"    {LIME}read{RESET}     {WHITE}[board]{RESET}                  read posts (all or one board)\n"
        f"    {LIME}boards{RESET}                            list all boards\n"
        f"    {LIME}users{RESET}                             list all users\n"
        f"    {LIME}search{RESET}   {WHITE}<keyword>{RESET}               search posts (case-insensitive)\n"
        f"\n"
        f"  {PURPLE}Interactive mode:{RESET}\n"
        f"    {LIME}register{RESET}                          create a new account\n"
        f"    {LIME}login{RESET}                             log in to a live session\n"
    )


def print_session_help() -> None:
    """Display help for the interactive session."""
    print(
        f"\n  {PURPLE}Session commands:{RESET}\n"
        f"    {LIME}post{RESET}   {WHITE}<message>{RESET}               post to general board\n"
        f"    {LIME}post{RESET}   {WHITE}#<board> <message>{RESET}      post to a specific board\n"
        f"    {LIME}read{RESET}   {WHITE}[board]{RESET}                 read posts (all or one board)\n"
        f"    {LIME}boards{RESET}                           list all boards\n"
        f"    {LIME}users{RESET}                            list all users\n"
        f"    {LIME}search{RESET} {WHITE}<keyword>{RESET}              search posts (case-insensitive)\n"
        f"    {LIME}whoami{RESET}                           show current user\n"
        f"    {LIME}help{RESET}                             show this help\n"
        f"    {LIME}quit{RESET}                             log out and exit\n"
    )


# ──────────────────────────────────────────────────────────────────────
#  Commands
# ──────────────────────────────────────────────────────────────────────

def cmd_post(username: str, board: str, message: str) -> None:
    """
    Insert a post into the posts table, creating the user on first sight.

    `board` is stored as a column on the row, not encoded into the
    table name, so no f-string SQL building is needed.  validate_board
    rejects unsafe values before they touch the database.
    """
    validate_board(board)

    with get_db() as conn:
        # INSERT OR IGNORE is a no-op when the username already exists.
        # That lets us use one path for brand-new and returning users.
        conn.execute(
            "INSERT OR IGNORE INTO users (username) VALUES (?)",
            (username,),
        )
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        user_id = row[0]

        conn.execute(
            "INSERT INTO posts (user_id, board, message, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (user_id, board, message,
             datetime.now().isoformat(timespec="seconds")),
        )

    print(f"  {LIME}Posted to {PURPLE}{board}{RESET}{LIME}.{RESET}")


def cmd_read(board: str | None = None) -> None:
    """
    Print posts in chronological order.

    If `board` is given, the WHERE clause narrows the result set.
    Otherwise we read every post across every board, still sorted
    by timestamp.  No UNION, no table enumeration — one query.
    """
    if board is not None:
        validate_board(board)

    with get_db() as conn:
        if board:
            rows = conn.execute(
                """
                SELECT u.username, p.message, p.timestamp, p.board
                  FROM posts p
                  JOIN users u ON p.user_id = u.id
                 WHERE p.board = ?
                 ORDER BY p.id ASC
                """,
                (board,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT u.username, p.message, p.timestamp, p.board
                  FROM posts p
                  JOIN users u ON p.user_id = u.id
                 ORDER BY p.id ASC
                """
            ).fetchall()

    if not rows:
        if board:
            print(f"\n  {DIM}No posts on {RESET}{PURPLE}{board}{RESET}{DIM} yet.{RESET}\n")
        else:
            print(f"\n  {DIM}No posts yet. Be the first to transmit.{RESET}\n")
        return

    label = f" on {PURPLE}{board}{RESET}" if board else ""
    print(f"\n  {DIM}── Posts{label} {'─' * 30}{RESET}")
    for username, message, timestamp, post_board in rows:
        print(format_post(username, message, timestamp, post_board))
    print()


def cmd_users() -> None:
    """
    Print each user who has posted, ordered by first appearance.

    Since users are only created at post time (INSERT OR IGNORE),
    every row in users has at least one post.  ORDER BY id preserves
    first-appearance order because ids are auto-incremented.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT username FROM users ORDER BY id ASC"
        ).fetchall()

    if not rows:
        print(f"\n  {DIM}No users yet.{RESET}\n")
        return

    print()
    for (username,) in rows:
        print(f"  {LIME}{username}{RESET}")
    print()


def cmd_boards() -> None:
    """
    List every board that has at least one post, with post counts.

    One GROUP BY query — boards come from the data, not from a
    separate registry.  Empty boards simply do not appear.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT board, COUNT(*) AS n
              FROM posts
             GROUP BY board
             ORDER BY n DESC, board ASC
            """
        ).fetchall()

    if not rows:
        print(f"\n  {DIM}No boards yet.{RESET}\n")
        return

    print()
    for board_name, count in rows:
        suffix = "s" if count != 1 else ""
        print(f"  {LIME}{board_name}{RESET} {DIM}({count} post{suffix}){RESET}")
    print()


def cmd_search(keyword: str) -> None:
    """
    Print all posts whose message contains `keyword` (case-insensitive).

    SQL version vs. JSON version
    ────────────────────────────
    Part A loads bbs.json into memory and loops through every post in
    Python — O(n) work in the application layer.  Here, a single
    query pushes the filtering into the database engine:

        WHERE p.message LIKE ?   with parameter "%keyword%"

    SQLite's LIKE is case-insensitive for ASCII by default, matching
    bbs.py's `.lower()` behaviour.

    INJECTION SAFETY
    ────────────────
    We build "%keyword%" in Python first, then pass it as a ?
    parameter.  The driver escapes the value before it reaches the
    engine.  What we never do:

        f"WHERE message LIKE '%{keyword}%'"   ← WRONG — interpolation

    If the keyword itself contains % or _, those will act as LIKE
    wildcards.  That's a reasonable power-user feature for a BBS
    search; the webserver API (A2) escapes them instead.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT u.username, p.message, p.timestamp, p.board
              FROM posts p
              JOIN users u ON p.user_id = u.id
             WHERE p.message LIKE ?
             ORDER BY p.timestamp ASC
            """,
            (f"%{keyword}%",),
        ).fetchall()

    if not rows:
        print(f"\n  {DIM}No posts match {RESET}{PURPLE}'{keyword}'{RESET}{DIM}.{RESET}\n")
        return

    print()
    for username, message, timestamp, board in rows:
        print(format_post(username, message, timestamp, board))
    print()


# ──────────────────────────────────────────────────────────────────────
#  Registration & Login
# ──────────────────────────────────────────────────────────────────────

def cmd_register() -> None:
    """
    Create a new user account with a password.

    Prompts interactively for username and password (password hidden).
    The password is hashed with PBKDF2-SHA256 before storage.

    Users who already exist from a CLI post but have no password can
    "claim" their account by registering — we UPDATE the NULL
    password_hash instead of rejecting them as a duplicate.
    """
    print(f"\n  {PURPLE}── Register {'─' * 30}{RESET}\n")

    username = input(f"  {WHITE}Username:{RESET} ").strip()
    if not username:
        print(f"  {PURPLE}Error:{RESET} username cannot be empty.")
        return

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing:
            if existing[1]:
                print(f"  {PURPLE}Error:{RESET} user {LIME}{username}{RESET} is already registered.")
                return
            # Username exists from a CLI post but has no password —
            # let the human who types this claim the account.
            pw = getpass.getpass(f"  {WHITE}Password:{RESET} ")
            if len(pw) < 1:
                print(f"  {PURPLE}Error:{RESET} password cannot be empty.")
                return
            pw2 = getpass.getpass(f"  {WHITE}Confirm:{RESET}  ")
            if pw != pw2:
                print(f"  {PURPLE}Error:{RESET} passwords do not match.")
                return
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(pw), existing[0]),
            )
            print(f"\n  {LIME}Password set for {PURPLE}{username}{RESET}{LIME}. You can now log in.{RESET}\n")
            return

    # Brand-new user path.
    pw = getpass.getpass(f"  {WHITE}Password:{RESET} ")
    if len(pw) < 1:
        print(f"  {PURPLE}Error:{RESET} password cannot be empty.")
        return
    pw2 = getpass.getpass(f"  {WHITE}Confirm:{RESET}  ")
    if pw != pw2:
        print(f"  {PURPLE}Error:{RESET} passwords do not match.")
        return

    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, hash_password(pw)),
        )
    print(f"\n  {LIME}Registered {PURPLE}{username}{RESET}{LIME}. You can now log in.{RESET}\n")


def cmd_login() -> None:
    """
    Authenticate a user then drop into an interactive session.

    Users without a password (CLI-created) are prompted to register.
    """
    print(f"\n  {PURPLE}── Login {'─' * 33}{RESET}\n")

    username = input(f"  {WHITE}Username:{RESET} ").strip()
    if not username:
        print(f"  {PURPLE}Error:{RESET} username cannot be empty.")
        return

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if not row:
        print(
            f"  {PURPLE}Error:{RESET} unknown user {WHITE}{username}{RESET}. "
            f"Run {LIME}register{RESET} first."
        )
        return

    user_id, pw_hash = row
    if not pw_hash:
        print(
            f"  {PURPLE}Error:{RESET} {WHITE}{username}{RESET} has no password. "
            f"Run {LIME}register{RESET} to set one."
        )
        return

    pw = getpass.getpass(f"  {WHITE}Password:{RESET} ")
    if not verify_password(pw, pw_hash):
        print(f"  {PURPLE}Error:{RESET} wrong password.")
        return

    # Success → enter the interactive session.  We discard user_id
    # deliberately — the session calls cmd_post(username, ...), which
    # looks up the id itself on every post.  Carrying a cached id
    # would invite consistency bugs (stale after the user is deleted).
    interactive_session(username)


# ──────────────────────────────────────────────────────────────────────
#  Interactive session  (Gold feature)
# ──────────────────────────────────────────────────────────────────────

def interactive_session(username: str) -> None:
    """
    REPL loop that keeps the user "logged in" with a jbbs> prompt.

    The username is implicit — posts don't require it as an argument.
    All one-shot commands (read, boards, users, search) work the same way.
    Type 'quit', 'exit', or 'logout' to leave.
    """
    print(
        f"\n  {LIME}Logged in as {PURPLE}{username}{RESET}"
        f"  {DIM}(type {RESET}{LIME}help{RESET}{DIM} for commands, "
        f"{RESET}{LIME}quit{RESET}{DIM} to leave){RESET}\n"
    )

    while True:
        try:
            raw = input(f"  {PURPLE}jbbs>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()

        cmd = parts[0].lower()

        if cmd in ("quit", "exit", "logout"):
            print(f"\n  {DIM}Goodbye, {RESET}{LIME}{username}{RESET}{DIM}.{RESET}\n")
            break

        elif cmd == "help":
            print_session_help()

        elif cmd == "whoami":
            print(f"\n  {LIME}{username}{RESET}\n")

        elif cmd == "post":
            if len(parts) < 2:
                print(f"  {PURPLE}Usage:{RESET} post {WHITE}[#board] <message>{RESET}")
                continue
            # A leading #board tag targets a specific board.
            # e.g.  post #tech Great article!
            # Without it, everything goes to "general".
            if parts[1].startswith("#") and len(parts[1]) > 1:
                board = parts[1][1:]   # strip the '#'
                message = " ".join(parts[2:]) if len(parts) >= 3 else ""
                if not message:
                    print(f"  {PURPLE}Usage:{RESET} post {WHITE}#board <message>{RESET}")
                    continue
            else:
                board = "general"
                message = " ".join(parts[1:])
            cmd_post(username, board, message)

        elif cmd == "read":
            board = parts[1] if len(parts) >= 2 else None
            cmd_read(board)

        elif cmd == "boards":
            cmd_boards()

        elif cmd == "users":
            cmd_users()

        elif cmd == "search":
            if len(parts) < 2:
                print(f"  {PURPLE}Usage:{RESET} search {WHITE}<keyword>{RESET}")
                continue
            keyword = " ".join(parts[1:])
            cmd_search(keyword)

        else:
            print(
                f"  {PURPLE}Unknown command:{RESET} {WHITE}{cmd}{RESET}  "
                f"{DIM}(type {RESET}{LIME}help{RESET}{DIM} for commands){RESET}"
            )


# ──────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────

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
                f"{WHITE}<username> [board] <message>{RESET}",
                file=sys.stderr,
            )
            sys.exit(1)
        username = sys.argv[2]
        if len(sys.argv) == 4:
            # post <username> <message>  →  board defaults to "general"
            board   = "general"
            message = sys.argv[3]
        else:
            # post <username> <board> <message...>
            board   = sys.argv[3]
            message = " ".join(sys.argv[4:])
        cmd_post(username, board, message)

    elif cmd == "read":
        board = sys.argv[2] if len(sys.argv) >= 3 else None
        cmd_read(board)

    elif cmd == "boards":
        cmd_boards()

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

    elif cmd == "register":
        cmd_register()

    elif cmd == "login":
        cmd_login()

    else:
        print(
            f"\n  {PURPLE}Unknown command:{RESET} {WHITE}{cmd}{RESET}\n"
            f"  Run {LIME}python bbs_db.py{RESET} with no arguments for help.\n",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
