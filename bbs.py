#!/usr/bin/env python3
"""
bbs.py  —  Part A: JSON-backed Bulletin Board System

Commands:
    python bbs.py post <username> <message>   post a new message
    python bbs.py read                         read all posts chronologically
    python bbs.py users                        list users who have posted
    python bbs.py search <keyword>             search messages (case-insensitive)

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

    Example output (with color): [2026-03-24 14:01] alice: Hello, world!
      - timestamp in purple
      - username in lime
      - message body in white
    """
    # Timestamps are stored as ISO-8601 ("2026-03-24T14:01:32").
    # Replace the T separator and drop seconds for a cleaner display.
    ts = post["timestamp"][:16].replace("T", " ")
    return (
        f"  {DIM}[{RESET}{PURPLE}{ts}{RESET}{DIM}]{RESET} "
        f"{LIME}{post['username']}{RESET}"
        f"{DIM}:{RESET} "
        f"{WHITE}{post['message']}{RESET}"
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
    Append a new post to bbs.json and print a confirmation.

    Timestamps use ISO-8601 with seconds precision ("2026-03-24T14:01:32").
    That format is both human-readable and lexicographically sortable,
    which will matter for Part B when we migrate this data into SQL.
    """
    posts = load_posts()
    posts.append({
        "username":  username,
        "message":   message,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })
    save_posts(posts)
    print(f"  {LIME}Posted.{RESET}")


def cmd_read() -> None:
    """
    Print all posts in chronological order (oldest first).

    Posts are stored in insertion order, so the JSON array is already the
    timeline — no sorting required.
    """
    posts = load_posts()
    if not posts:
        print(f"\n  {DIM}No posts yet. Be the first to transmit.{RESET}\n")
        return
    print()
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
        # Minimum: bbs.py post <username> <at-least-one-word>
        if len(sys.argv) < 4:
            print(
                f"  {PURPLE}Usage:{RESET} python bbs.py post "
                f"{WHITE}<username> <message>{RESET}",
                file=sys.stderr,
            )
            sys.exit(1)
        username = sys.argv[2]
        # Join all remaining args so quoting is optional:
        #   python bbs.py post alice hello world       ← works
        #   python bbs.py post alice "hello world"     ← also works
        message = " ".join(sys.argv[3:])
        cmd_post(username, message)

    elif cmd == "read":
        cmd_read()

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
