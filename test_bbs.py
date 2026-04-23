#!/usr/bin/env python3
"""
test_bbs.py  —  End-to-end test suite for JBBS (Parts A, B, and C).

Runs bbs.py, bbs_db.py, and migrate.py as subprocesses — the same way
a user would — and checks their output and side effects (JSON file
contents, DB rows).  pytest is the runner.

ISOLATION MODEL
───────────────
Every test should be able to run in any order, on its own.  That is
enforced by the `isolate` autouse fixture below: before each test it
deletes bbs.json, bbs.db, and any bbs_backup_*.db files, then does
the same after the test finishes.  No test needs to call cleanup()
itself, and no test can leave state behind that a later test relies
on.

WHY WE RUN THE REAL SCRIPTS (INSTEAD OF IMPORTING THEIR FUNCTIONS)
───────────────────────────────────────────────────────────────────
Subprocesses exercise the argv parsing, the exit codes, the printed
output, and the on-disk side effects — all the things a human user
actually experiences.  Importing cmd_post() directly would skip all
of that.  The trade-off is a slower suite (~2s for ~30 tests), which
is fine for a project this size.
"""

import subprocess
import os
import sys
import json
import sqlite3
import re
import glob

import pytest

# ──────────────────────────────────────────────────────────────────────
#  Paths
# ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE  = os.path.join(SCRIPT_DIR, "bbs.json")
DB_FILE    = os.path.join(SCRIPT_DIR, "bbs.db")


# ──────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes so we can assert on plain text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def run(script: str, *args: str) -> tuple[str, int]:
    """
    Run a JBBS script as a subprocess and return (plain-text output, exit code).

    stdout and stderr are merged and ANSI-stripped so tests can do simple
    string-in-string checks without worrying about color codes.
    """
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, script), *args],
        capture_output=True,
        text=True,
    )
    combined = result.stdout + result.stderr
    return strip_ansi(combined), result.returncode


def run_interactive(script: str, *args: str, stdin_text: str = "") -> tuple[str, int]:
    """
    Run a JBBS script with stdin piped in (for register/login prompts).

    Setting TERM=dumb persuades getpass to read from the piped stdin
    instead of looking for a tty.
    """
    env = os.environ.copy()
    env["TERM"] = "dumb"
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, script), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
    )
    combined = result.stdout + result.stderr
    return strip_ansi(combined), result.returncode


def _remove_artifacts() -> None:
    """Delete every file a JBBS script might create in the script dir."""
    for path in [JSON_FILE, DB_FILE]:
        if os.path.exists(path):
            os.remove(path)
    for path in glob.glob(os.path.join(SCRIPT_DIR, "bbs_backup_*.db")):
        os.remove(path)


def read_json() -> list[dict]:
    """Load bbs.json and return its contents."""
    with open(JSON_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def query_db(sql: str, params: tuple = ()) -> list[tuple]:
    """Run a read-only query against bbs.db and return all rows."""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


# ──────────────────────────────────────────────────────────────────────
#  Autouse isolation fixture
# ──────────────────────────────────────────────────────────────────────
#
# `autouse=True` attaches this fixture to every test in the module
# without anyone having to ask for it.  The double cleanup (before
# AND after) means we handle two cases at once:
#   - a brand-new run with stale files lying around from some prior
#     invocation of the scripts,
#   - a test that happens to fail mid-run and would otherwise leave
#     poisoned state behind for whatever runs next.
@pytest.fixture(autouse=True)
def isolate():
    _remove_artifacts()
    yield
    _remove_artifacts()


# ══════════════════════════════════════════════════════════════════════
#  Part A — JSON storage (bbs.py)
# ══════════════════════════════════════════════════════════════════════

def test_json_read_empty():
    """Reading with no bbs.json prints a friendly empty-state message."""
    # Run `bbs.py read` with no data file present.
    output, code = run("bbs.py", "read")

    # Should exit cleanly (0) and print a helpful empty-state message,
    # not crash or show a traceback.
    assert code == 0
    assert "No posts" in output


def test_json_post_and_read():
    """Posts are written to bbs.json and read back in order."""
    # Post two messages from different users.
    run("bbs.py", "post", "alice", "Hello world")
    run("bbs.py", "post", "bob", "Hi Alice")

    # Open bbs.json directly and verify the raw data: exactly 2 post
    # objects in the array, with correct usernames and messages in
    # insertion order.
    posts = read_json()
    assert len(posts) == 2, f"expected 2 posts, got {len(posts)}"
    assert posts[0]["username"] == "alice"
    assert posts[0]["message"] == "Hello world"
    assert posts[1]["username"] == "bob"
    assert posts[1]["message"] == "Hi Alice"

    # Also verify that `bbs.py read` prints both messages.
    output, code = run("bbs.py", "read")
    assert code == 0
    assert "Hello world" in output
    assert "Hi Alice" in output


def test_json_multi_word_unquoted():
    """Multiple unquoted args after the board name are joined into one message."""
    # bbs.py joins sys.argv[4:] into the message when a board is given.
    run("bbs.py", "post", "alice", "general", "this", "is", "many", "words")

    posts = read_json()
    assert posts[0]["message"] == "this is many words"


def test_json_users_order():
    """Users are listed in first-appearance order."""
    # Post order: bob first, then alice, then bob again.
    run("bbs.py", "post", "bob", "first")
    run("bbs.py", "post", "alice", "second")
    run("bbs.py", "post", "bob", "third")

    # bob posted first and should appear before alice.  Also: bob
    # should appear only once despite posting twice.
    output, code = run("bbs.py", "users")
    assert code == 0
    assert output.index("bob") < output.index("alice"), "bob should appear before alice"


def test_json_search_hit():
    """Search finds posts containing the keyword."""
    run("bbs.py", "post", "alice", "Hello world")
    run("bbs.py", "post", "bob", "Goodbye world")

    output, _ = run("bbs.py", "search", "Hello")
    assert "Hello world" in output
    assert "Goodbye" not in output


def test_json_search_case_insensitive():
    """Search is case-insensitive: 'hello' matches 'Hello'."""
    run("bbs.py", "post", "alice", "Hello world")

    output, _ = run("bbs.py", "search", "hello")
    assert "Hello world" in output


def test_json_search_no_match():
    """Search with no results prints a friendly message, not an error."""
    run("bbs.py", "post", "alice", "Hello")
    output, code = run("bbs.py", "search", "zzzzz")

    assert code == 0
    assert "No posts match" in output


# ══════════════════════════════════════════════════════════════════════
#  Part B — SQLite storage (bbs_db.py)
# ══════════════════════════════════════════════════════════════════════

def test_db_post_and_read():
    """Posts are inserted into the DB and read back correctly."""
    run("bbs_db.py", "post", "alice", "DB hello")
    run("bbs_db.py", "post", "bob", "DB hi")

    # Under the flat schema, every post lives in the single `posts`
    # table with board='general'.
    users = query_db("SELECT * FROM users ORDER BY id")
    posts = query_db(
        "SELECT * FROM posts WHERE board = 'general' ORDER BY id"
    )
    assert len(users) == 2
    assert users[0][1] == "alice"   # users.username is column 1
    assert len(posts) == 2

    output, code = run("bbs_db.py", "read")
    assert code == 0
    assert "DB hello" in output
    assert "DB hi" in output


def test_db_repeat_user():
    """The same username posting twice should create only one user row."""
    run("bbs_db.py", "post", "alice", "msg 1")
    run("bbs_db.py", "post", "alice", "msg 2")

    users = query_db("SELECT * FROM users")
    posts = query_db("SELECT * FROM posts WHERE board = 'general'")
    assert len(users) == 1, f"expected 1 user, got {len(users)}"
    assert len(posts) == 2


def test_db_users_order():
    """Users are listed in first-post order, just like the JSON version."""
    run("bbs_db.py", "post", "bob", "first")
    run("bbs_db.py", "post", "alice", "second")

    output, code = run("bbs_db.py", "users")
    assert code == 0
    assert output.index("bob") < output.index("alice")


def test_db_search():
    """SQL search with LIKE finds the right posts (case-insensitive)."""
    run("bbs_db.py", "post", "alice", "Hello world")
    run("bbs_db.py", "post", "bob", "Goodbye world")

    # Lowercase "hello" should match because SQLite's LIKE is
    # case-insensitive for ASCII by default.
    output, _ = run("bbs_db.py", "search", "hello")
    assert "Hello world" in output
    assert "Goodbye" not in output


def test_db_search_no_match():
    """SQL search with zero results prints a friendly message."""
    run("bbs_db.py", "post", "alice", "Hello")
    output, code = run("bbs_db.py", "search", "zzzzz")

    assert code == 0
    assert "No posts match" in output


def test_db_foreign_keys_valid():
    """Every post should reference a valid user (no orphaned foreign keys)."""
    run("bbs_db.py", "post", "alice", "test")
    run("bbs_db.py", "post", "bob", "test")

    # LEFT JOIN posts → users; any row where u.id IS NULL is orphaned.
    orphans = query_db(
        """
        SELECT p.id FROM posts p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE u.id IS NULL
        """
    )
    assert len(orphans) == 0, f"found orphaned posts: {orphans}"


def test_db_read_empty():
    """Reading an empty DB prints a friendly message, not an error."""
    output, code = run("bbs_db.py", "read")

    assert code == 0
    assert "No posts" in output


def test_db_posts_across_boards():
    """Posts to different boards all live in the same posts table."""
    # Three posts to three boards — all are stored as rows in `posts`
    # with a `board` column.  This test is new: under the old
    # table-per-board design it was implicit; making it explicit now
    # locks in the flat-schema intent.
    run("bbs_db.py", "post", "alice", "general", "hi all")
    run("bbs_db.py", "post", "alice", "tech", "linux is great")
    run("bbs_db.py", "post", "bob", "music", "new album")

    boards = query_db(
        "SELECT board, COUNT(*) FROM posts GROUP BY board ORDER BY board"
    )
    assert boards == [("general", 1), ("music", 1), ("tech", 1)]

    # `bbs_db.py boards` should list all three, newest-count first.
    out, code = run("bbs_db.py", "boards")
    assert code == 0
    assert "general" in out
    assert "tech" in out
    assert "music" in out


def test_db_boards_filter_reads_one_board():
    """`read <board>` scopes results to exactly that board."""
    run("bbs_db.py", "post", "alice", "tech", "SQLite rocks")
    run("bbs_db.py", "post", "bob", "music", "new album")

    out, _ = run("bbs_db.py", "read", "tech")
    assert "SQLite rocks" in out
    assert "new album" not in out


# ══════════════════════════════════════════════════════════════════════
#  Part C — Migration (migrate.py)
# ══════════════════════════════════════════════════════════════════════

def test_migrate_clean():
    """Basic migration: JSON → empty DB."""
    run("bbs.py", "post", "alice", "json msg 1")
    run("bbs.py", "post", "bob", "json msg 2")

    output, code = run("migrate.py")

    assert code == 0
    assert "Migrated 2 post(s)" in output

    db_output, _ = run("bbs_db.py", "read")
    assert "json msg 1" in db_output
    assert "json msg 2" in db_output


def test_migrate_preserves_timestamps():
    """Migrated posts keep their original JSON timestamps, not 'now'."""
    # Hand-write bbs.json with a known, past timestamp.  The migrator
    # should copy that timestamp verbatim into the DB.
    fake_ts = "2025-06-15T08:30:00"
    posts = [{"username": "alice", "message": "old post", "timestamp": fake_ts}]
    with open(JSON_FILE, "w") as fh:
        json.dump(posts, fh)

    run("migrate.py")

    # Every migrated post lives in `posts`; we expect the one row we
    # put in to come back with the exact ISO string we supplied.
    rows = query_db("SELECT timestamp FROM posts")
    assert rows[0][0] == fake_ts, f"timestamp changed: {rows[0][0]}"


def _seed_merge_scenario() -> None:
    """
    Set up the merge scenario: two JSON posts (Jan, Mar) plus one DB
    post whose timestamp falls between them (Feb).

    Pulled into a helper so the merge test and the backup test can
    both reproduce the same state without depending on each other.
    """
    json_posts = [
        {"username": "alice", "message": "january", "timestamp": "2026-01-01T10:00:00"},
        {"username": "bob",   "message": "march",   "timestamp": "2026-03-01T10:00:00"},
    ]
    with open(JSON_FILE, "w") as fh:
        json.dump(json_posts, fh)

    # Seed the DB with one post, then hand-update its timestamp to
    # land in February — between the two JSON posts chronologically.
    run("bbs_db.py", "post", "carol", "placeholder")
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "UPDATE posts SET timestamp = '2026-02-01T10:00:00', message = 'february'"
    )
    conn.commit()
    conn.close()


def test_migrate_merge_chronological_ids():
    """
    Merge migration re-inserts all posts so IDs match chronological order.

    This is the critical edge case.  When bbs.db already has posts,
    migrate.py should (1) back up the existing DB, (2) read posts
    from BOTH sources, (3) wipe, (4) re-insert everything sorted by
    timestamp.  After the migration, post IDs should align with the
    chronological order (id 1 = earliest).

    We also assert on the backup file existing here, folding in what
    used to be a separate test_migrate_backup_created test.  The two
    checks are really facets of one behaviour: "merge migration
    preserves data via a backup and reorders by time."
    """
    _seed_merge_scenario()

    output, code = run("migrate.py")
    assert code == 0
    assert "Migrated 3 post(s)" in output
    assert "Backed up" in output

    # Chronological order: id 1 = alice (Jan) → id 2 = carol (Feb) → id 3 = bob (Mar).
    rows = query_db(
        """
        SELECT p.id, u.username, p.timestamp
          FROM posts p JOIN users u ON p.user_id = u.id
         ORDER BY p.id
        """
    )
    assert len(rows) == 3
    assert rows[0][1] == "alice"  and "2026-01" in rows[0][2]
    assert rows[1][1] == "carol"  and "2026-02" in rows[1][2]
    assert rows[2][1] == "bob"    and "2026-03" in rows[2][2]

    # Walk the rows pairwise and confirm timestamps never decrease.
    for i in range(len(rows) - 1):
        assert rows[i][2] <= rows[i + 1][2], (
            f"id {rows[i][0]} ({rows[i][2]}) is later than "
            f"id {rows[i + 1][0]} ({rows[i + 1][2]})"
        )

    # Folded-in backup check: a bbs_backup_*.db file should exist.
    backups = glob.glob(os.path.join(SCRIPT_DIR, "bbs_backup_*.db"))
    assert len(backups) >= 1, "expected at least one backup file"


def test_migrate_no_json():
    """Running migrate.py with no bbs.json exits cleanly."""
    output, code = run("migrate.py")

    assert code == 0
    assert "Nothing to migrate" in output


def test_migrate_output_matches():
    """After migration, bbs_db.py read should show the same posts as bbs.py read."""
    run("bbs.py", "post", "alice", "same output test")
    run("bbs.py", "post", "bob", "should match")

    run("migrate.py")

    db_output, _ = run("bbs_db.py", "read")
    assert "same output test" in db_output
    assert "should match" in db_output


# ══════════════════════════════════════════════════════════════════════
#  Gold — Registration, Login & Interactive Session
# ══════════════════════════════════════════════════════════════════════

def test_register_new_user():
    """Register a brand-new user with matching passwords."""
    # stdin: username, password, confirm password
    output, code = run_interactive(
        "bbs_db.py", "register",
        stdin_text="testuser\nsecret123\nsecret123\n",
    )
    assert code == 0
    assert "Registered" in output or "registered" in output.lower()

    # Verify the user row was created with a password hash.
    rows = query_db(
        "SELECT username, password_hash FROM users WHERE username = 'testuser'"
    )
    assert len(rows) == 1
    assert rows[0][1] is not None  # password_hash should be set


def test_register_duplicate_user():
    """Registering an already-registered user prints an error."""
    # Self-contained: register testuser FIRST in this test, then try
    # to register again.  We used to lean on test_register_new_user
    # having run first — bad idea, since pytest does not guarantee
    # ordering and parallel runners would break.
    run_interactive(
        "bbs_db.py", "register",
        stdin_text="testuser\nsecret123\nsecret123\n",
    )

    output, code = run_interactive(
        "bbs_db.py", "register",
        stdin_text="testuser\nsecret123\nsecret123\n",
    )
    assert "already registered" in output.lower()


def test_register_password_mismatch():
    """Mismatched passwords should be rejected."""
    output, code = run_interactive(
        "bbs_db.py", "register",
        stdin_text="mismatchuser\nabc\nxyz\n",
    )
    assert "do not match" in output.lower()

    # User should NOT have been created.
    rows = query_db("SELECT * FROM users WHERE username = 'mismatchuser'")
    assert len(rows) == 0


def test_register_claim_existing_cli_user():
    """A user created via CLI post (no password) can register to set one."""
    # Create a user via one-shot post (no password).
    run("bbs_db.py", "post", "cliuser", "hello from CLI")

    # That user should exist but with no password.
    rows = query_db(
        "SELECT password_hash FROM users WHERE username = 'cliuser'"
    )
    assert len(rows) == 1
    assert rows[0][0] is None

    # Now register that user — should set their password.
    output, code = run_interactive(
        "bbs_db.py", "register",
        stdin_text="cliuser\nmypass\nmypass\n",
    )
    assert code == 0
    assert "Password set" in output or "password set" in output.lower()

    # Password hash should now be populated.
    rows = query_db(
        "SELECT password_hash FROM users WHERE username = 'cliuser'"
    )
    assert rows[0][0] is not None


def test_login_unknown_user():
    """Logging in with a non-existent username prints an error."""
    output, code = run_interactive(
        "bbs_db.py", "login",
        stdin_text="nobody\n",
    )
    assert "unknown user" in output.lower() or "register" in output.lower()


def test_login_wrong_password():
    """Logging in with the wrong password is rejected."""
    # Register a user first so there's something to mis-authenticate against.
    run_interactive("bbs_db.py", "register", stdin_text="secuser\ncorrect\ncorrect\n")

    output, code = run_interactive(
        "bbs_db.py", "login",
        stdin_text="secuser\nwrong\n",
    )
    assert "wrong password" in output.lower()


def test_interactive_session():
    """Log in and run commands inside the interactive session."""
    run_interactive("bbs_db.py", "register", stdin_text="sessuser\npass\npass\n")

    # Login + session commands piped via stdin.
    # Commands: whoami, post a message, read, quit
    session_input = "\n".join([
        "sessuser",      # username prompt
        "pass",          # password prompt
        "whoami",        # session command
        "post Hello from session!",
        "read",
        "quit",
    ]) + "\n"

    output, code = run_interactive("bbs_db.py", "login", stdin_text=session_input)
    assert code == 0
    assert "sessuser" in output                 # whoami output
    assert "Hello from session!" in output      # post should appear in read
    assert "Goodbye" in output                  # quit message


def test_interactive_post_to_board():
    """Session post with a #board prefix posts to that board."""
    run_interactive("bbs_db.py", "register", stdin_text="boarduser\npass\npass\n")

    session_input = "\n".join([
        "boarduser",
        "pass",
        "post #tech Discussing databases",
        "boards",
        "read tech",
        "quit",
    ]) + "\n"

    output, code = run_interactive("bbs_db.py", "login", stdin_text=session_input)
    assert code == 0
    assert "tech" in output
    assert "Discussing databases" in output


# ══════════════════════════════════════════════════════════════════════
#  Error handling
# ══════════════════════════════════════════════════════════════════════

def test_error_unknown_command():
    """An unknown command exits with code 1 and a useful message."""
    # Checked against both CLI entry points.
    for script in ("bbs.py", "bbs_db.py"):
        output, code = run(script, "badcmd")
        assert code == 1, f"{script} should exit 1"
        assert "Unknown command" in output


def test_error_post_missing_args():
    """'post' with no username/message exits with code 1."""
    for script in ("bbs.py", "bbs_db.py"):
        _, code = run(script, "post")
        assert code == 1, f"{script} post with no args should exit 1"


def test_error_search_missing_args():
    """'search' with no keyword exits with code 1."""
    for script in ("bbs.py", "bbs_db.py"):
        _, code = run(script, "search")
        assert code == 1, f"{script} search with no args should exit 1"
