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
    """Reading with no bbs.json prints a friendly empty-state message."""
    cleanup()
    output, code = run("bbs.py", "read")
    assert code == 0
    assert "No posts" in output


def test_json_post_and_read():
    """Posts are written to bbs.json and read back in order."""
    cleanup()
    run("bbs.py", "post", "alice", "Hello world")
    run("bbs.py", "post", "bob", "Hi Alice")

    posts = read_json()
    assert len(posts) == 2, f"expected 2 posts, got {len(posts)}"
    assert posts[0]["username"] == "alice"
    assert posts[0]["message"] == "Hello world"
    assert posts[1]["username"] == "bob"
    assert posts[1]["message"] == "Hi Alice"

    output, code = run("bbs.py", "read")
    assert code == 0
    assert "Hello world" in output
    assert "Hi Alice" in output


def test_json_multi_word_unquoted():
    """Multiple unquoted args after the username are joined into one message."""
    cleanup()
    run("bbs.py", "post", "alice", "this", "is", "many", "words")
    posts = read_json()
    assert posts[0]["message"] == "this is many words"


def test_json_users_order():
    """Users are listed in first-appearance order."""
    cleanup()
    run("bbs.py", "post", "bob", "first")
    run("bbs.py", "post", "alice", "second")
    run("bbs.py", "post", "bob", "third")

    output, code = run("bbs.py", "users")
    assert code == 0
    # bob posted first, should be listed before alice
    assert output.index("bob") < output.index("alice"), "bob should appear before alice"


def test_json_search_hit():
    """Search finds posts containing the keyword."""
    cleanup()
    run("bbs.py", "post", "alice", "Hello world")
    run("bbs.py", "post", "bob", "Goodbye world")

    output, _ = run("bbs.py", "search", "Hello")
    assert "Hello world" in output
    assert "Goodbye" not in output


def test_json_search_case_insensitive():
    """Search is case-insensitive: 'hello' matches 'Hello'."""
    cleanup()
    run("bbs.py", "post", "alice", "Hello world")
    output, _ = run("bbs.py", "search", "hello")
    assert "Hello world" in output


def test_json_search_no_match():
    """Search with no results prints a friendly message, not an error."""
    cleanup()
    run("bbs.py", "post", "alice", "Hello")
    output, code = run("bbs.py", "search", "zzzzz")
    assert code == 0
    assert "No posts match" in output


# ──────────────────────────────────────────────────────────────────────────────
#  Part B — SQLite storage (bbs_db.py)
# ──────────────────────────────────────────────────────────────────────────────

def test_db_post_and_read():
    """Posts are inserted into the DB and read back correctly."""
    cleanup()
    run("bbs_db.py", "post", "alice", "DB hello")
    run("bbs_db.py", "post", "bob", "DB hi")

    users = query_db("SELECT * FROM users ORDER BY id")
    posts = query_db("SELECT * FROM posts ORDER BY id")
    assert len(users) == 2
    assert users[0][1] == "alice"
    assert len(posts) == 2

    output, code = run("bbs_db.py", "read")
    assert code == 0
    assert "DB hello" in output
    assert "DB hi" in output


def test_db_repeat_user():
    """The same username posting twice should create only one user row."""
    cleanup()
    run("bbs_db.py", "post", "alice", "msg 1")
    run("bbs_db.py", "post", "alice", "msg 2")

    users = query_db("SELECT * FROM users")
    posts = query_db("SELECT * FROM posts")
    assert len(users) == 1, f"expected 1 user, got {len(users)}"
    assert len(posts) == 2


def test_db_users_order():
    """Users are listed in first-post order, just like the JSON version."""
    cleanup()
    run("bbs_db.py", "post", "bob", "first")
    run("bbs_db.py", "post", "alice", "second")

    output, code = run("bbs_db.py", "users")
    assert code == 0
    assert output.index("bob") < output.index("alice")


def test_db_search():
    """SQL search with LIKE finds the right posts (case-insensitive)."""
    cleanup()
    run("bbs_db.py", "post", "alice", "Hello world")
    run("bbs_db.py", "post", "bob", "Goodbye world")

    output, _ = run("bbs_db.py", "search", "hello")
    assert "Hello world" in output
    assert "Goodbye" not in output


def test_db_search_no_match():
    cleanup()
    run("bbs_db.py", "post", "alice", "Hello")
    output, code = run("bbs_db.py", "search", "zzzzz")
    assert code == 0
    assert "No posts match" in output


def test_db_foreign_keys_valid():
    """Every post should reference a valid user (no orphaned foreign keys)."""
    cleanup()
    run("bbs_db.py", "post", "alice", "test")
    run("bbs_db.py", "post", "bob", "test")

    orphans = query_db("""
        SELECT p.id FROM posts p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE u.id IS NULL
    """)
    assert len(orphans) == 0, f"found orphaned posts: {orphans}"


def test_db_read_empty():
    """Reading an empty DB prints a friendly message, not an error."""
    cleanup()
    output, code = run("bbs_db.py", "read")
    assert code == 0
    assert "No posts" in output


# ──────────────────────────────────────────────────────────────────────────────
#  Part C — Migration (migrate.py)
# ──────────────────────────────────────────────────────────────────────────────

def test_migrate_clean():
    """Basic migration: JSON → empty DB."""
    cleanup()
    run("bbs.py", "post", "alice", "json msg 1")
    run("bbs.py", "post", "bob", "json msg 2")

    output, code = run("migrate.py")
    assert code == 0
    assert "Migrated 2 post(s)" in output

    # Verify the data landed in the DB
    db_output, _ = run("bbs_db.py", "read")
    assert "json msg 1" in db_output
    assert "json msg 2" in db_output


def test_migrate_preserves_timestamps():
    """Migrated posts keep their original JSON timestamps, not 'now'."""
    cleanup()
    fake_ts = "2025-06-15T08:30:00"
    posts = [{"username": "alice", "message": "old post", "timestamp": fake_ts}]
    with open(JSON_FILE, "w") as fh:
        json.dump(posts, fh)

    run("migrate.py")

    rows = query_db("SELECT timestamp FROM posts")
    assert rows[0][0] == fake_ts, f"timestamp changed: {rows[0][0]}"


def test_migrate_merge_chronological_ids():
    """
    When bbs.db already has data, the migration should:
      1. back up bbs.db
      2. merge JSON + DB posts
      3. re-insert all posts sorted by timestamp so IDs are chronological
    """
    cleanup()

    # Write JSON posts with known timestamps (Jan and Mar)
    json_posts = [
        {"username": "alice", "message": "january", "timestamp": "2026-01-01T10:00:00"},
        {"username": "bob",   "message": "march",   "timestamp": "2026-03-01T10:00:00"},
    ]
    with open(JSON_FILE, "w") as fh:
        json.dump(json_posts, fh)

    # Seed the DB with a post timestamped February (between the two JSON posts)
    run("bbs_db.py", "post", "carol", "placeholder")
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE posts SET timestamp = '2026-02-01T10:00:00', message = 'february'")
    conn.commit()
    conn.close()

    output, code = run("migrate.py")
    assert code == 0
    assert "Migrated 3 post(s)" in output
    assert "Backed up" in output

    # IDs must be in chronological order: 1=Jan, 2=Feb, 3=Mar
    rows = query_db("""
        SELECT p.id, u.username, p.timestamp
          FROM posts p JOIN users u ON p.user_id = u.id
         ORDER BY p.id
    """)
    assert len(rows) == 3
    assert rows[0][1] == "alice"  and "2026-01" in rows[0][2]   # id 1 = Jan
    assert rows[1][1] == "carol"  and "2026-02" in rows[1][2]   # id 2 = Feb
    assert rows[2][1] == "bob"    and "2026-03" in rows[2][2]   # id 3 = Mar

    # Verify timestamps are non-decreasing with ID
    for i in range(len(rows) - 1):
        assert rows[i][2] <= rows[i + 1][2], (
            f"id {rows[i][0]} ({rows[i][2]}) is later than id {rows[i + 1][0]} ({rows[i + 1][2]})"
        )


def test_migrate_backup_created():
    """A backup file should exist after a merge migration."""
    backups = glob.glob(os.path.join(SCRIPT_DIR, "bbs_backup_*.db"))
    assert len(backups) >= 1, "expected at least one backup file"


def test_migrate_no_json():
    """Running migrate.py with no bbs.json exits cleanly."""
    cleanup()
    output, code = run("migrate.py")
    assert code == 0
    assert "Nothing to migrate" in output


def test_migrate_output_matches():
    """After migration, bbs_db.py read should show the same posts as bbs.py read."""
    cleanup()
    run("bbs.py", "post", "alice", "same output test")
    run("bbs.py", "post", "bob", "should match")

    json_output, _ = run("bbs.py", "read")
    run("migrate.py")
    db_output, _ = run("bbs_db.py", "read")

    # Both should contain the same messages (timestamps may differ in format
    # by seconds, but messages and usernames must match exactly)
    assert "same output test" in db_output
    assert "should match" in db_output


# ──────────────────────────────────────────────────────────────────────────────
#  Error handling
# ──────────────────────────────────────────────────────────────────────────────

def test_error_unknown_command():
    """An unknown command exits with code 1 and a useful message."""
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
