"""
bbs_ui.py  —  shared terminal-formatting code for every JBBS entry point.

WHY THIS FILE EXISTS
────────────────────
bbs.py (JSON backend) and bbs_db.py (SQLite backend) are two programs
that render the same BBS to the same kind of terminal.  Before this
module existed, both files carried their own copies of:

  - the ANSI color palette,
  - the ASCII-art splash banner, and
  - the per-post line formatter.

That meant any tweak — a new color, a cleaner timestamp, a bug in the
formatter — had to be made in two places.  It also meant the two
programs could silently drift apart.  This module is the single
source of truth for anything that draws BBS text to a terminal.

Contents:
  - Color constants (LIME, PURPLE, WHITE, DIM, RESET)
  - make_banner(label) — the ASCII-art splash with a version tag
  - format_post(...)   — one post → one colored terminal line
"""

# ──────────────────────────────────────────────────────────────────────
#  Terminal color constants  (ANSI 256-color escape codes)
#
#  These are zero-width control strings; the terminal interprets them
#  as color-switch instructions without consuming any visible space,
#  which matters for the banner's fixed-width box alignment.
#
#    LIME   → usernames, success feedback, command names in help
#    PURPLE → timestamps, box borders, labels, error prefixes
#    WHITE  → message body (legible on dark backgrounds)
#    DIM    → secondary / decorative text (punctuation, separators)
# ──────────────────────────────────────────────────────────────────────
LIME   = "\033[38;5;118m"
PURPLE = "\033[38;5;135m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def make_banner(label: str) -> str:
    """
    Build the splash banner with a version-specific label.

    The banner's bottom section has a fixed 52-column interior.
    Count the visible chars on the label line:

        "  " + "JACK'S BULLETIN BOARD SYSTEM" + "  " + "//" + "  " + label_area
         2  +               28                +  2  +  2   +  2  +    16       = 52

    So the label area is 16 columns wide.  Labels longer than that
    will push the closing ║ out of alignment; the only real callers
    pass short strings ("JSON v1.0", "SQLITE v2.0") that fit comfortably.
    """
    # Ljust to the fixed 16-column label area.
    padded_label = f"{label:<16}"
    return (
        "\n"
        f"  {PURPLE}╔{'═' * 52}╗{RESET}\n"
        f"  {PURPLE}║  {LIME}     ██╗ ██████╗ ██████╗ ███████╗{PURPLE}                 ║{RESET}\n"
        f"  {PURPLE}║  {LIME}     ██║ ██╔══██╗██╔══██╗██╔════╝{PURPLE}                 ║{RESET}\n"
        f"  {PURPLE}║  {LIME}     ██║ ██████╔╝██████╔╝███████╗{PURPLE}                 ║{RESET}\n"
        f"  {PURPLE}║  {LIME}██   ██║ ██╔══██╗██╔══██╗╚════██║{PURPLE}                 ║{RESET}\n"
        f"  {PURPLE}║  {LIME}╚█████╔╝ ██████╔╝██████╔╝███████║{PURPLE}                 ║{RESET}\n"
        f"  {PURPLE}║  {LIME} ╚════╝  ╚═════╝ ╚═════╝ ╚══════╝{PURPLE}                 ║{RESET}\n"
        f"  {PURPLE}║{'':52}║{RESET}\n"
        f"  {PURPLE}║  {LIME}JACK'S BULLETIN BOARD SYSTEM{PURPLE}  {DIM}//{RESET}  {WHITE}{padded_label}{RESET}{PURPLE}║{RESET}\n"
        f"  {PURPLE}║{'':52}║{RESET}\n"
        f"  {PURPLE}╚{'═' * 52}╝{RESET}\n"
    )


def format_post(username: str, message: str, timestamp: str,
                board: str | None = None) -> str:
    """
    Render one post as a colored terminal line.

    Signature takes plain values — not a dict or a row object — so
    every caller (JSON loader, SQLite row, migration script) can use
    the same formatter without wrapping/unwrapping.

    The timestamp is sliced to 16 chars ("YYYY-MM-DDTHH:MM") and the
    literal T is replaced with a space so the output reads like a
    date instead of an ISO-8601 string.

    A board tag is only shown for NON-general boards.  "general" is
    the implicit default, so tagging every general post with
    [general] would be visual noise.
    """
    ts = timestamp[:16].replace("T", " ")
    board_tag = ""
    if board and board != "general":
        board_tag = f"{DIM}[{RESET}{PURPLE}{board}{RESET}{DIM}]{RESET} "
    return (
        f"  {DIM}[{RESET}{PURPLE}{ts}{RESET}{DIM}]{RESET} "
        f"{board_tag}"
        f"{LIME}{username}{RESET}"
        f"{DIM}:{RESET} "
        f"{WHITE}{message}{RESET}"
    )
