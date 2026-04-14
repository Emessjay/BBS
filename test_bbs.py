#!/usr/bin/env python3
"""
test_bbs.py  —  End-to-end test suite for the BBS system (Parts A, B, and C).

Runs bbs.py, bbs_db.py, and migrate.py as subprocesses — the same way a user
would — and checks their output and side effects (JSON file contents, DB rows).
All generated files are removed when the suite finishes, even if a test fails.

Usage:
    python test_bbs.py
"""

import subprocess
import os
import sys
import json
import sqlite3
import re
import glob
import time

# ──────────────────────────────────────────────────────────────────────────────
#  Paths & colors
# ──────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE  = os.path.join(SCRIPT_DIR, "bbs.json")
DB_FILE    = os.path.join(SCRIPT_DIR, "bbs.db")

LIME   = "\033[38;5;118m"
RED    = "\033[38;5;196m"
PURPLE = "\033[38;5;135m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes so we can assert on plain text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def run(script: str, *args: str) -> tuple[str, int]:
    """
    Run a BBS script as a subprocess and return (plain-text output, exit code).

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


def cleanup() -> None:
    """Remove every file the BBS scripts might create."""
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


# ──────────────────────────────────────────────────────────────────────────────
#  Part A — JSON storage (bbs.py)
# ──────────────────────────────────────────────────────────────────────────────

def test_json_read_empty():
    # Test: running `bbs.py read` when bbs.json doesn't exist yet should
    # handle the missing file gracefully and print a friendly message.
    """Reading with no bbs.json prints a friendly empty-state message."""
    # Start fresh — no bbs.json on disk.
    cleanup()

    # Run `bbs.py read` with no data file present.
    output, code = run("bbs.py", "read")

    # Should exit cleanly (0) and print a helpful empty-state message,
    # not crash or show a traceback.
    assert code == 0
    assert "No posts" in output


def test_json_post_and_read():
    # Test: post two messages, then verify both the raw JSON file contents
    # and the `bbs.py read` terminal output contain the correct data.
    """Posts are written to bbs.json and read back in order."""
    cleanup()

    # Post two messages from different users.
    run("bbs.py", "post", "alice", "Hello world")
    run("bbs.py", "post", "bob", "Hi Alice")

    # Open bbs.json directly and verify the raw data:
    # - exactly 2 post objects in the array
    # - correct usernames and messages in insertion order
    posts = read_json()
    assert len(posts) == 2, f"expected 2 posts, got {len(posts)}"
    assert posts[0]["username"] == "alice"
    assert posts[0]["message"] == "Hello world"
    assert posts[1]["username"] == "bob"
    assert posts[1]["message"] == "Hi Alice"

    # Also verify that `bbs.py read` prints both messages back to the terminal.
    output, code = run("bbs.py", "read")
    assert code == 0
    assert "Hello world" in output
    assert "Hi Alice" in output


def test_json_multi_word_unquoted():
    # Test: bbs.py joins sys.argv[4:] with spaces for the message when a
    # board is specified, so passing separate unquoted words after the board
    # name should be stored as a single message string.
    # (With the boards feature, argv[3] is the board name and argv[4:] is
    # the message — so unquoted multi-word messages work after the board.)
    """Multiple unquoted args after the board name are joined into one message."""
    cleanup()

    # Pass a board name, then several separate words as individual CLI args.
    # bbs.py joins sys.argv[4:] into the message.
    run("bbs.py", "post", "alice", "general", "this", "is", "many", "words")

    # Verify the JSON file contains one message with all words joined.
    posts = read_json()
    assert posts[0]["message"] == "this is many words"


def test_json_users_order():
    # Test: `bbs.py users` should list users in order of their first post,
    # and a user who posted multiple times should only appear once.
    """Users are listed in first-appearance order."""
    cleanup()

    # Post in a specific order: bob first, then alice, then bob again.
    run("bbs.py", "post", "bob", "first")
    run("bbs.py", "post", "alice", "second")
    run("bbs.py", "post", "bob", "third")

    # `bbs.py users` should list bob before alice (he posted first) and
    # should NOT list bob twice despite his two posts.
    output, code = run("bbs.py", "users")
    assert code == 0
    assert output.index("bob") < output.index("alice"), "bob should appear before alice"


def test_json_search_hit():
    # Test: `bbs.py search` should return only posts whose message
    # contains the keyword, and exclude posts that don't match.
    """Search finds posts containing the keyword."""
    cleanup()

    # Create two posts — only one contains the word "Hello".
    run("bbs.py", "post", "alice", "Hello world")
    run("bbs.py", "post", "bob", "Goodbye world")

    # Search for "Hello" and verify only the matching post is returned,
    # not the "Goodbye" post.
    output, _ = run("bbs.py", "search", "Hello")
    assert "Hello world" in output
    assert "Goodbye" not in output


def test_json_search_case_insensitive():
    # Test: searching with a different case than the original message
    # should still return a match (bbs.py lowercases both sides).
    """Search is case-insensitive: 'hello' matches 'Hello'."""
    cleanup()

    # Post a message with a capital "H".
    run("bbs.py", "post", "alice", "Hello world")

    # Search with a lowercase "h" — should still find it because
    # bbs.py lowercases both the keyword and each message before comparing.
    output, _ = run("bbs.py", "search", "hello")
    assert "Hello world" in output


def test_json_search_no_match():
    # Test: a search that matches nothing should still exit 0 and show
    # a "no match" message rather than empty output or a crash.
    """Search with no results prints a friendly message, not an error."""
    cleanup()

    # Create one post, then search for a keyword that doesn't appear in it.
    run("bbs.py", "post", "alice", "Hello")
    output, code = run("bbs.py", "search", "zzzzz")

    # Should still exit 0 (not an error) and show a "no match" message.
    assert code == 0
    assert "No posts match" in output


# ──────────────────────────────────────────────────────────────────────────────
#  Part B — SQLite storage (bbs_db.py)
# ──────────────────────────────────────────────────────────────────────────────

def test_db_post_and_read():
    # Test: post two messages via bbs_db.py, then verify they land in the
    # SQLite tables correctly AND show up in the `read` output.
    """Posts are inserted into the DB and read back correctly."""
    cleanup()

    # Post two messages from different users through the CLI.
    run("bbs_db.py", "post", "alice", "DB hello")
    run("bbs_db.py", "post", "bob", "DB hi")

    # Query the DB directly to verify the schema is populated:
    # - 2 rows in users, alice first (lower id because she posted first)
    # - 2 rows in board_general (posts default to the "general" board)
    users = query_db("SELECT * FROM users ORDER BY id")
    posts = query_db("SELECT * FROM board_general ORDER BY id")
    assert len(users) == 2
    assert users[0][1] == "alice"
    assert len(posts) == 2

    # Verify `bbs_db.py read` renders both messages to the terminal.
    output, code = run("bbs_db.py", "read")
    assert code == 0
    assert "DB hello" in output
    assert "DB hi" in output


def test_db_repeat_user():
    # Test: posting twice with the same username should NOT create a
    # duplicate row in the users table.  INSERT OR IGNORE handles this.
    """The same username posting twice should create only one user row."""
    cleanup()

    # alice posts two separate messages.
    run("bbs_db.py", "post", "alice", "msg 1")
    run("bbs_db.py", "post", "alice", "msg 2")

    # The users table should have exactly 1 row (alice), but the
    # board_general table should have 2 rows — both referencing that
    # single user id.
    users = query_db("SELECT * FROM users")
    posts = query_db("SELECT * FROM board_general")
    assert len(users) == 1, f"expected 1 user, got {len(users)}"
    assert len(posts) == 2


def test_db_users_order():
    # Test: `bbs_db.py users` should list users in the order of their first
    # post, matching the JSON version's behaviour.  The SQL uses
    # ORDER BY MIN(p.id) to achieve this.
    """Users are listed in first-post order, just like the JSON version."""
    cleanup()

    # bob posts first, then alice.
    run("bbs_db.py", "post", "bob", "first")
    run("bbs_db.py", "post", "alice", "second")

    # bob should appear before alice in the output.
    output, code = run("bbs_db.py", "users")
    assert code == 0
    assert output.index("bob") < output.index("alice")


def test_db_search():
    # Test: the SQL LIKE query should find matching posts and exclude
    # non-matching ones.  SQLite's LIKE is case-insensitive for ASCII
    # by default, so searching "hello" should match "Hello world".
    """SQL search with LIKE finds the right posts (case-insensitive)."""
    cleanup()

    # Create two posts — only one contains "Hello".
    run("bbs_db.py", "post", "alice", "Hello world")
    run("bbs_db.py", "post", "bob", "Goodbye world")

    # Search with lowercase "hello"; only alice's post should appear.
    output, _ = run("bbs_db.py", "search", "hello")
    assert "Hello world" in output
    assert "Goodbye" not in output


def test_db_search_no_match():
    # Test: searching for a keyword that doesn't exist in any post should
    # exit cleanly and print a "no match" message, not crash or return junk.
    """SQL search with zero results prints a friendly message."""
    cleanup()

    # Post one message, then search for something completely unrelated.
    run("bbs_db.py", "post", "alice", "Hello")
    output, code = run("bbs_db.py", "search", "zzzzz")

    # Should exit 0 with a user-friendly "no match" message.
    assert code == 0
    assert "No posts match" in output


def test_db_foreign_keys_valid():
    # Test: every post's user_id should point to a real row in the users
    # table.  A LEFT JOIN that finds NULL on the users side means a post
    # exists without a matching user — a broken foreign key.
    """Every post should reference a valid user (no orphaned foreign keys)."""
    cleanup()

    # Create posts from two different users.
    run("bbs_db.py", "post", "alice", "test")
    run("bbs_db.py", "post", "bob", "test")

    # LEFT JOIN board_general → users; any row where u.id IS NULL is an orphan.
    orphans = query_db("""
        SELECT p.id FROM board_general p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE u.id IS NULL
    """)
    assert len(orphans) == 0, f"found orphaned posts: {orphans}"


def test_db_read_empty():
    # Test: running `bbs_db.py read` on a fresh database (tables exist but
    # are empty) should print an empty-state message, not crash.
    """Reading an empty DB prints a friendly message, not an error."""
    cleanup()

    # No posts have been made — DB will be created fresh by init_db().
    output, code = run("bbs_db.py", "read")

    # Should exit 0 with a "no posts" message.
    assert code == 0
    assert "No posts" in output


# ──────────────────────────────────────────────────────────────────────────────
#  Part C — Migration (migrate.py)
# ──────────────────────────────────────────────────────────────────────────────

def test_migrate_clean():
    # Test: the simplest migration path — bbs.json has data, bbs.db does
    # not exist.  migrate.py should create the DB and insert every JSON post.
    """Basic migration: JSON → empty DB."""
    cleanup()

    # Seed bbs.json with two posts via bbs.py.
    run("bbs.py", "post", "alice", "json msg 1")
    run("bbs.py", "post", "bob", "json msg 2")

    # Run the migration script.
    output, code = run("migrate.py")

    # Should report success and the correct post count.
    assert code == 0
    assert "Migrated 2 post(s)" in output

    # Verify the data is now readable through bbs_db.py.
    db_output, _ = run("bbs_db.py", "read")
    assert "json msg 1" in db_output
    assert "json msg 2" in db_output


def test_migrate_preserves_timestamps():
    # Test: migrate.py should carry over the original timestamps from
    # bbs.json verbatim — not replace them with the current time.
    # We write a bbs.json by hand with a hardcoded past timestamp,
    # migrate it, then check the DB has that exact same timestamp.
    """Migrated posts keep their original JSON timestamps, not 'now'."""
    cleanup()

    # Manually write a bbs.json with a known, past timestamp.
    fake_ts = "2025-06-15T08:30:00"
    posts = [{"username": "alice", "message": "old post", "timestamp": fake_ts}]
    with open(JSON_FILE, "w") as fh:
        json.dump(posts, fh)

    # Run the migration.
    run("migrate.py")

    # The timestamp in the DB should be the exact string from the JSON,
    # not today's date.  Posts default to board_general.
    rows = query_db("SELECT timestamp FROM board_general")
    assert rows[0][0] == fake_ts, f"timestamp changed: {rows[0][0]}"


def test_migrate_merge_chronological_ids():
    # Test: this is the critical edge case.  When bbs.db already has posts,
    # migrate.py should:
    #   1. back up the existing bbs.db
    #   2. read posts out of BOTH bbs.json and the existing bbs.db
    #   3. wipe the DB and re-insert everything sorted by timestamp
    # The result: post IDs are always chronological (id 1 = earliest).
    #
    # To verify this, we create a 3-post scenario where the DB post's
    # timestamp falls *between* the two JSON posts' timestamps.  After
    # migration, the DB post should have the middle id, not the last one.
    """Merge migration re-inserts all posts so IDs match chronological order."""
    cleanup()

    # Manually write bbs.json with two posts: January and March.
    json_posts = [
        {"username": "alice", "message": "january", "timestamp": "2026-01-01T10:00:00"},
        {"username": "bob",   "message": "march",   "timestamp": "2026-03-01T10:00:00"},
    ]
    with open(JSON_FILE, "w") as fh:
        json.dump(json_posts, fh)

    # Seed bbs.db with a post, then manually overwrite its timestamp to
    # February — placing it chronologically between the two JSON posts.
    # Posts default to the board_general table.
    run("bbs_db.py", "post", "carol", "placeholder")
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE board_general SET timestamp = '2026-02-01T10:00:00', message = 'february'")
    conn.commit()
    conn.close()

    # Run the migration.  It should detect existing DB data, back it up,
    # merge all 3 posts, and re-insert them sorted by timestamp.
    output, code = run("migrate.py")
    assert code == 0
    assert "Migrated 3 post(s)" in output
    assert "Backed up" in output

    # Query the rebuilt DB and check that IDs are chronological:
    #   id 1 = alice (Jan)  →  id 2 = carol (Feb)  →  id 3 = bob (Mar)
    # All posts land in board_general since that's the default board.
    rows = query_db("""
        SELECT p.id, u.username, p.timestamp
          FROM board_general p JOIN users u ON p.user_id = u.id
         ORDER BY p.id
    """)
    assert len(rows) == 3
    assert rows[0][1] == "alice"  and "2026-01" in rows[0][2]   # id 1 = Jan
    assert rows[1][1] == "carol"  and "2026-02" in rows[1][2]   # id 2 = Feb
    assert rows[2][1] == "bob"    and "2026-03" in rows[2][2]   # id 3 = Mar

    # Walk the rows pairwise and confirm timestamps never decrease with id.
    for i in range(len(rows) - 1):
        assert rows[i][2] <= rows[i + 1][2], (
            f"id {rows[i][0]} ({rows[i][2]}) is later than id {rows[i + 1][0]} ({rows[i + 1][2]})"
        )


def test_migrate_backup_created():
    # Test: the previous test (merge migration) should have created a
    # bbs_backup_<timestamp>.db file.  This test intentionally depends
    # on that state — it runs immediately after the merge test.
    """A backup file should exist after a merge migration."""

    # Glob for any backup files in the project directory.
    backups = glob.glob(os.path.join(SCRIPT_DIR, "bbs_backup_*.db"))
    assert len(backups) >= 1, "expected at least one backup file"


def test_migrate_no_json():
    # Test: if bbs.json doesn't exist, there's nothing to migrate.
    # migrate.py should exit 0 with a helpful message, not crash.
    """Running migrate.py with no bbs.json exits cleanly."""
    cleanup()

    # No bbs.json on disk — run migrate.py.
    output, code = run("migrate.py")

    # Should exit cleanly and explain that there's nothing to do.
    assert code == 0
    assert "Nothing to migrate" in output


def test_migrate_output_matches():
    # Test: the whole point of the migration is that `bbs_db.py read`
    # should show the same posts as `bbs.py read` for the same data.
    # We seed JSON, migrate, and compare the DB output.
    """After migration, bbs_db.py read should show the same posts as bbs.py read."""
    cleanup()

    # Create two posts via the JSON version.
    run("bbs.py", "post", "alice", "same output test")
    run("bbs.py", "post", "bob", "should match")

    # Migrate JSON → SQLite.
    run("migrate.py")

    # Read back through bbs_db.py and confirm both messages appear.
    db_output, _ = run("bbs_db.py", "read")
    assert "same output test" in db_output
    assert "should match" in db_output


# ──────────────────────────────────────────────────────────────────────────────
#  Error handling
# ──────────────────────────────────────────────────────────────────────────────

def test_error_unknown_command():
    # Test: passing a command that doesn't exist (e.g. "badcmd") should
    # exit with code 1 and print "Unknown command", not a Python traceback.
    # Checked against both bbs.py and bbs_db.py.
    """An unknown command exits with code 1 and a useful message."""
    for script in ("bbs.py", "bbs_db.py"):
        output, code = run(script, "badcmd")
        assert code == 1, f"{script} should exit 1"
        assert "Unknown command" in output


def test_error_post_missing_args():
    # Test: running `post` with no username or message should print a
    # usage hint and exit 1, not crash with an IndexError.
    # Checked against both bbs.py and bbs_db.py.
    """'post' with no username/message exits with code 1."""
    for script in ("bbs.py", "bbs_db.py"):
        _, code = run(script, "post")
        assert code == 1, f"{script} post with no args should exit 1"


def test_error_search_missing_args():
    # Test: running `search` with no keyword should print a usage hint
    # and exit 1, not crash with an IndexError.
    # Checked against both bbs.py and bbs_db.py.
    """'search' with no keyword exits with code 1."""
    for script in ("bbs.py", "bbs_db.py"):
        _, code = run(script, "search")
        assert code == 1, f"{script} search with no args should exit 1"


# ──────────────────────────────────────────────────────────────────────────────
#  Test runner
# ──────────────────────────────────────────────────────────────────────────────

# All tests in execution order.  Tests within a section may depend on the
# state left by earlier tests in that section (e.g. migrate_backup_created
# checks for the backup file written by migrate_merge_chronological_ids).
TESTS = [
    # Part A
    test_json_read_empty,
    test_json_post_and_read,
    test_json_multi_word_unquoted,
    test_json_users_order,
    test_json_search_hit,
    test_json_search_case_insensitive,
    test_json_search_no_match,
    # Part B
    test_db_read_empty,
    test_db_post_and_read,
    test_db_repeat_user,
    test_db_users_order,
    test_db_search,
    test_db_search_no_match,
    test_db_foreign_keys_valid,
    # Part C
    test_migrate_no_json,
    test_migrate_clean,
    test_migrate_preserves_timestamps,
    test_migrate_merge_chronological_ids,
    test_migrate_backup_created,           # depends on merge test above
    test_migrate_output_matches,
    # Errors
    test_error_unknown_command,
    test_error_post_missing_args,
    test_error_search_missing_args,
]


def main() -> None:
    print(f"\n  {PURPLE}BBS Test Suite{RESET}  {DIM}({len(TESTS)} tests){RESET}\n")

    passed = 0
    failed = 0
    t0 = time.time()

    for test_fn in TESTS:
        name = test_fn.__name__
        try:
            test_fn()
            print(f"  {LIME}PASS{RESET}  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  {RED}FAIL{RESET}  {name}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  {RED}ERR {RESET}  {name}: {type(exc).__name__}: {exc}")
            failed += 1

    elapsed = time.time() - t0

    # Always clean up, even after failures.
    cleanup()

    colour = LIME if failed == 0 else RED
    print(
        f"\n  {colour}{passed} passed{RESET}, "
        f"{colour}{failed} failed{RESET}  "
        f"{DIM}({elapsed:.1f}s){RESET}\n"
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
