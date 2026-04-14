#!/usr/bin/env python3
"""
bbs.py  —  Part A: JSON-backed Bulletin Board System (Silver: boards)

Commands:
    python bbs.py post <username> <message>               post to "general"
    python bbs.py post <username> <board> <message>       post to a board
    python bbs.py read                                     read all posts
    python bbs.py read <board>                             read one board
    python bbs.py boards                                   list all boards
    python bbs.py users                                    list all users
    python bbs.py search <keyword>                         search posts

All data is stored as a flat JSON array in bbs.json.
No third-party dependencies — pure Python stdlib.
"""

import sys
import json
import os
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
#  Terminal color constants  (ANSI 256-color escape codes)
#
#  These are zero-width control strings; the terminal interprets them as
#  color-switch instructions without consuming any visible space.
#
#   LIME   → usernames, success feedback, command names in help
#   PURPLE → timestamps, box borders, labels, error prefixes
#   WHITE  → message body (legible on dark backgrounds)
#   DIM    → secondary / decorative text (punctuation, separators)
# ──────────────────────────────────────────────────────────────────────────────
LIME   = "\033[38;5;118m"
PURPLE = "\033[38;5;135m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# JSON storage file — lives next to this script, not the CWD.
# Using abspath so the script can be called from any directory.
BBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bbs.json")

# ──────────────────────────────────────────────────────────────────────────────
#  Splash banner  (only shown when bbs.py is run with no arguments)
#
#  Box geometry: 2-char indent  +  ║  +  44-char inner  +  ║  =  48 visible chars/row.
#  ANSI escape codes are zero-width so they never break this alignment.
#  The '═' * 44 and f"{'':44}" expressions build the borders/padding correctly.
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
    f"  {PURPLE}║  {LIME}BULLETIN BOARD SYSTEM{PURPLE}  {DIM}//{RESET}  {WHITE}JSON v1.0{RESET}      {PURPLE}║{RESET}\n"
    f"  {PURPLE}║{'':44}║{RESET}\n"
    f"  {PURPLE}╚{'═' * 44}╝{RESET}\n"
)


# ──────────────────────────────────────────────────────────────────────────────
#  JSON I/O helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_posts() -> list[dict]:
    """
    Load all posts from bbs.json.

    Returns an empty list on first run (file doesn't exist yet).
    Exits with an error message if the file exists but is corrupt JSON.
    """
    if not os.path.exists(BBS_FILE):
        return []
    try:
        with open(BBS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        print(f"  {PURPLE}Error:{RESET} bbs.json is corrupt — {exc}", file=sys.stderr)
        sys.exit(1)


def save_posts(posts: list[dict]) -> None:
    """
    Write the posts list back to bbs.json with readable 2-space indentation.

    json.dumps builds the full string in memory first, then we write it in
    one shot — slightly safer than streaming json.dump if the process is
    interrupted mid-write.
    """
    payload = json.dumps(posts, indent=2, ensure_ascii=False)
    with open(BBS_FILE, "w", encoding="utf-8") as fh:
        fh.write(payload)


# ──────────────────────────────────────────────────────────────────────────────
#  Display helpers
# ──────────────────────────────────────────────────────────────────────────────

def format_post(post: dict) -> str:
    """
    Render a single post record as a colored terminal line.

    Shows a board tag for non-general boards.
    """
    ts = post["timestamp"][:16].replace("T", " ")
    board = post.get("board", "general")
    board_tag = ""
    if board != "general":
        board_tag = f"{DIM}[{RESET}{PURPLE}{board}{RESET}{DIM}]{RESET} "
    return (
        f"  {DIM}[{RESET}{PURPLE}{ts}{RESET}{DIM}]{RESET} "
        f"{board_tag}"
        f"{LIME}{post['username']}{RESET}"
        f"{DIM}:{RESET} "
        f"{WHITE}{post['message']}{RESET}"
    )


def print_help() -> None:
    """Display the splash banner followed by a compact command reference."""
    print(BANNER)
    print(
        f"  {PURPLE}Commands:{RESET}\n"
        f"    {LIME}post{RESET}   {WHITE}<user> <message>{RESET}         post to general board\n"
        f"    {LIME}post{RESET}   {WHITE}<user> <board> <message>{RESET} post to a specific board\n"
        f"    {LIME}read{RESET}   {WHITE}[board]{RESET}                  read posts (all or one board)\n"
        f"    {LIME}boards{RESET}                          list all boards\n"
        f"    {LIME}users{RESET}                           list all users\n"
        f"    {LIME}search{RESET} {WHITE}<keyword>{RESET}               search posts (case-insensitive)\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Commands
# ──────────────────────────────────────────────────────────────────────────────

def cmd_post(username: str, board: str, message: str) -> None:
    """
    Append a new post to bbs.json and print a confirmation.
    """
    posts = load_posts()
    posts.append({
        "username":  username,
        "board":     board,
        "message":   message,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })
    save_posts(posts)
    print(f"  {LIME}Posted to {PURPLE}{board}{RESET}{LIME}.{RESET}")


def cmd_read(board: str | None = None) -> None:
    """
    Print posts in chronological order (oldest first).

    If board is given, only show posts from that board.
    """
    posts = load_posts()
    if board:
        posts = [p for p in posts if p.get("board", "general") == board]

    if not posts:
        if board:
            print(f"\n  {DIM}No posts on {RESET}{PURPLE}{board}{RESET}{DIM} yet.{RESET}\n")
        else:
            print(f"\n  {DIM}No posts yet. Be the first to transmit.{RESET}\n")
        return

    label = f" on {PURPLE}{board}{RESET}" if board else ""
    print(f"\n  {DIM}── Posts{label} {'─' * 30}{RESET}")
    for post in posts:
        print(format_post(post))
    print()


def cmd_users() -> None:
    """
    Print each unique username, in order of their first post.

    Preserves first-appearance order rather than sorting alphabetically —
    it reflects who arrived on the board first, which feels right for a BBS.

    Uses a dict as an ordered set: dict keys are insertion-ordered since
    Python 3.7, and assigning None to a key we've already seen is a no-op.
    """
    posts = load_posts()
    if not posts:
        print(f"\n  {DIM}No users yet.{RESET}\n")
        return

    seen: dict[str, None] = {}
    for post in posts:
        seen[post["username"]] = None   # no-op if already present

    print()
    for username in seen:
        print(f"  {LIME}{username}{RESET}")
    print()


def cmd_boards() -> None:
    """List all boards that have at least one post, with post counts."""
    posts = load_posts()
    if not posts:
        print(f"\n  {DIM}No boards yet.{RESET}\n")
        return

    counts: dict[str, int] = {}
    for post in posts:
        b = post.get("board", "general")
        counts[b] = counts.get(b, 0) + 1

    # Sort by count descending
    sorted_boards = sorted(counts.items(), key=lambda x: x[1], reverse=True)

    print()
    for board_name, count in sorted_boards:
        print(f"  {LIME}{board_name}{RESET} {DIM}({count} post{'s' if count != 1 else ''}){RESET}")
    print()


def cmd_search(keyword: str) -> None:
    """
    Print all posts whose message contains the keyword (case-insensitive).

    This is a brute-force linear scan: we load the entire file and check
    every message. That's fine for a small BBS, but it means the work grows
    linearly with the number of posts.  Part B replaces this with a single
    SQL WHERE ... LIKE query that the database can index.
    """
    posts = load_posts()
    kw_lower = keyword.lower()
    results = [p for p in posts if kw_lower in p["message"].lower()]

    if not results:
        print(f"\n  {DIM}No posts match {RESET}{PURPLE}'{keyword}'{RESET}{DIM}.{RESET}\n")
        return

    print()
    for post in results:
        print(format_post(post))
    print()


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Parse sys.argv and dispatch to the appropriate command function."""

    # No arguments → show help and exit cleanly (not an error).
    if len(sys.argv) < 2:
        print_help()
        return

    cmd = sys.argv[1].lower()

    if cmd == "post":
        if len(sys.argv) < 4:
            print(
                f"  {PURPLE}Usage:{RESET} python bbs.py post "
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
                f"  {PURPLE}Usage:{RESET} python bbs.py search {WHITE}<keyword>{RESET}",
                file=sys.stderr,
            )
            sys.exit(1)
        # Same join trick — multi-word searches work without quotes.
        keyword = " ".join(sys.argv[2:])
        cmd_search(keyword)

    else:
        print(
            f"\n  {PURPLE}Unknown command:{RESET} {WHITE}{cmd}{RESET}\n"
            f"  Run {LIME}python bbs.py{RESET} with no arguments for help.\n",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
