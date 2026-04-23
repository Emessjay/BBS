"""
conftest.py — pytest fixtures shared by every test in this directory.

WHY THIS FILE EXISTS
────────────────────
FastAPI apps talk to a database through a module-level connection helper
(db.get_db).  If every test used the same bbs.db file we would have two
problems:

  1. Tests would see each other's users and posts, making assertions
     about "how many users exist" flaky.
  2. A test that left the DB in a weird state could poison unrelated
     tests that ran after it.

The fix is a fresh SQLite file per test.  We do that by pointing db.py
at a brand-new path (via the BBS_DB_FILE env var) before each test, then
wrapping the FastAPI app in a TestClient.

HOW THE FIXTURE WIRES TOGETHER
──────────────────────────────
    tmp_path         → pytest built-in; a per-test temp directory
    monkeypatch      → pytest built-in; undoes env changes after the test
    BBS_DB_FILE      → our own convention; db.py reads it on every
                       connection, so changing it between tests just works
    TestClient       → FastAPI's in-process HTTP client (built on httpx).
                       Using it as a context manager (`with ... as c:`)
                       triggers the app's lifespan handler, which in turn
                       calls init_db() to create tables in the fresh file.
"""

import os
import sys
import pytest
from fastapi.testclient import TestClient


# Make `import main` and `import db` work when pytest is run from either
# the project root or the webserver/ directory.  Prepending this file's
# directory to sys.path is the simplest way to achieve that without
# turning webserver/ into a package with an __init__.py.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """
    Yield a TestClient pointed at an empty, brand-new SQLite DB.

    Each test that asks for `client` gets its own tmp_path, which means
    its own database file, which means zero bleed-through from other
    tests.
    """
    # Point db.py at a file that does not yet exist.  init_db() will
    # CREATE TABLE on first connection.
    db_path = tmp_path / "bbs.db"
    monkeypatch.setenv("BBS_DB_FILE", str(db_path))

    # Import lazily — after the env var is set — so any module-level
    # state inside main/db that DOES happen to read BBS_DB_FILE at
    # import time sees our temp path, not the default.
    import main

    # The `with` form runs the lifespan handler, which calls init_db().
    # Exiting the `with` block runs shutdown (none, in our case).
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def alice(client):
    """Convenience: a user named 'alice' already exists."""
    r = client.post("/users", json={"username": "alice"})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def bob(client):
    """Convenience: a user named 'bob' already exists."""
    r = client.post("/users", json={"username": "bob"})
    assert r.status_code == 201, r.text
    return r.json()
