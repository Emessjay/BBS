"""
main.py — FastAPI BBS webserver (Assignment 2, bronze tier).

READING GUIDE
─────────────
This file has four layers, top to bottom:

  1. Lifespan + app setup    — runs init_db() on startup.
  2. Pydantic models         — request validation (UserCreate,
                               PostCreate) and response shape lockdown
                               (UserOut, PostOut).
  3. Row → dict helpers      — translate sqlite3.Row objects into the
                               exact-shape dicts the spec requires.
  4. Route handlers          — one function per endpoint, grouped by
                               resource.  Raw SQL (no ORM) because
                               A1 used raw SQL too and the schema
                               is small enough that the extra
                               machinery would obscure the logic.

WHY PYDANTIC MODELS FOR BOTH REQUESTS AND RESPONSES
────────────────────────────────────────────────────
The spec says response bodies must contain EXACTLY the listed fields
— no extras, no omissions.  The naive implementation is to return
`dict(row)` straight from SQLite, which leaks internal columns like
`user_id`.  By declaring a `response_model=UserOut` (or PostOut) on
each route, FastAPI filters the outgoing dict against the model's
fields before serialising.  Any stray field silently drops; any
missing field raises a server error that surfaces in tests.  That
gives us field-shape correctness for free.

WHY RAW SQL INSTEAD OF AN ORM
──────────────────────────────
SQLAlchemy would add 200+ lines of boilerplate for a schema with two
tables and seven queries.  Raw SQL parameterised with `?` is safe
against injection (sqlite3 binds values properly), readable, and
mirrors A1's teaching so students can trace from one assignment to
the next.  Silver/gold tiers are where the ORM starts paying for
itself — not here.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
import sqlite3

from fastapi import FastAPI, HTTPException, Header, Query, Response, status
from pydantic import BaseModel, Field

from db import get_db, init_db


# ──────────────────────────────────────────────────────────────────────
#  Lifespan
# ──────────────────────────────────────────────────────────────────────
#
# FastAPI runs this async context manager around the app's lifetime:
# code before `yield` executes on startup; code after `yield` executes
# on shutdown.  We use it to call init_db() so the schema is ready
# before the first request arrives.
#
# TestClient(app) as a context manager triggers this same lifespan,
# which is why every fresh test DB gets its tables created
# automatically (no manual setup in conftest.py).
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="BBS Webserver", lifespan=lifespan)


# ──────────────────────────────────────────────────────────────────────
#  Pydantic models
# ──────────────────────────────────────────────────────────────────────
#
# Incoming models validate request bodies.  If validation fails,
# FastAPI returns 422 with the Pydantic error detail — we do not
# have to write that plumbing ourselves.
#
# Outgoing models (UserOut, PostOut) are what response_model= hooks
# into to filter the shape of what we return.  Declaring them
# explicitly is the single most effective way to stop database
# columns from leaking into the public API.

class UserCreate(BaseModel):
    """Body for POST /users."""
    # Field(..., min_length=..., max_length=..., pattern=...) generates
    # the exact validation rules listed in the assignment:
    #   - 3 to 20 chars
    #   - regex ^[a-zA-Z0-9_]+$
    # The "..." sentinel means "required" (Pydantic v2 idiom).
    username: str = Field(..., min_length=3, max_length=20,
                          pattern=r"^[a-zA-Z0-9_]+$")


class PostCreate(BaseModel):
    """Body for POST /posts."""
    # min_length=1 catches the empty-message case; max_length=500 is
    # the spec cap.  Both failures produce 422.
    message: str = Field(..., min_length=1, max_length=500)


class UserOut(BaseModel):
    """What /users endpoints return.  Exactly two fields — no more."""
    username: str
    created_at: str


class PostOut(BaseModel):
    """What /posts endpoints return.  Exactly four fields."""
    id: int
    username: str
    message: str
    created_at: str


# ──────────────────────────────────────────────────────────────────────
#  Time & row helpers
# ──────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """
    Current UTC time in the spec's format: YYYY-MM-DDTHH:MM:SS.

    We strip the tzinfo so isoformat() does not append "+00:00" — the
    spec's example shows no timezone suffix, and adding one would
    cause the verifier's regex-style assertions to fail.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _row_to_user(row: sqlite3.Row) -> dict:
    """
    Turn a `users` row into a UserOut-shaped dict.

    Called explicitly (instead of returning `dict(row)`) because
    `dict(row)` would include `id`, which is NOT in the public user
    shape.  One line here is cheaper than a mystery field leak.
    """
    return {"username": row["username"], "created_at": row["created_at"]}


def _row_to_post(row: sqlite3.Row) -> dict:
    """
    Turn a joined posts + users row into a PostOut-shaped dict.

    Expects the query to SELECT p.id, u.username, p.message,
    p.created_at (in any order) so the column names line up with
    the PostOut model.
    """
    return {
        "id": row["id"],
        "username": row["username"],
        "message": row["message"],
        "created_at": row["created_at"],
    }


# ──────────────────────────────────────────────────────────────────────
#  /users
# ──────────────────────────────────────────────────────────────────────

@app.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def create_user(body: UserCreate):
    """
    Create a new user.

    Returns 201 on success, 409 if the username is already taken,
    422 if the username fails validation (handled by Pydantic before
    this function even runs).
    """
    created_at = _now_iso()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, created_at) VALUES (?, ?)",
                (body.username, created_at),
            )
    except sqlite3.IntegrityError:
        # UNIQUE constraint violation on the username column.  That
        # is the ONLY IntegrityError we expect here — any other
        # integrity error is a bug, not a user-facing 409.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"username '{body.username}' already exists",
        )
    return {"username": body.username, "created_at": created_at}


@app.get("/users", response_model=list[UserOut])
def list_users():
    """List every user.  Returns [] (NOT 404) when there are none."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT username, created_at FROM users ORDER BY id"
        ).fetchall()
    return [_row_to_user(r) for r in rows]


@app.get("/users/{username}", response_model=UserOut)
def get_user(username: str):
    """Look up one user by username.  404 if they do not exist."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT username, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user '{username}' not found",
        )
    return _row_to_user(row)


@app.get("/users/{username}/posts", response_model=list[PostOut])
def list_user_posts(username: str):
    """
    All posts by one user.

    Two cases to distinguish:
      - user does not exist       → 404
      - user exists, has no posts → 200 with []

    We do a cheap existence check first, then the join.  The
    alternative (one query with LEFT JOIN) conflates the two cases
    and makes it harder to return the right status code.
    """
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user '{username}' not found",
            )
        rows = conn.execute(
            """
            SELECT p.id, u.username, p.message, p.created_at
              FROM posts p
              JOIN users u ON u.id = p.user_id
             WHERE u.username = ?
             ORDER BY p.id
            """,
            (username,),
        ).fetchall()
    return [_row_to_post(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────
#  /posts
# ──────────────────────────────────────────────────────────────────────

@app.post("/posts", status_code=status.HTTP_201_CREATED, response_model=PostOut)
def create_post(
    body: PostCreate,
    # Header(default=None, alias="X-Username") means:
    #   - missing header  → x_username is None
    #   - empty header    → x_username is ""
    # Both cases are treated as "client forgot to send identity" and
    # produce 400 below.  We intentionally do NOT use the Pydantic
    # pattern=... validator on this header, because a malformed
    # X-Username should become a 400 or 404 (per spec), not 422.
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
):
    """
    Create a post on behalf of the user named in X-Username.

    Status codes:
      201 — created
      400 — missing or empty X-Username header
      404 — X-Username names a user that does not exist
      422 — message fails validation (Pydantic)
    """
    # Missing OR empty header → 400.  Empty string is treated the
    # same as missing because it cannot possibly be a valid username
    # and the "client bug" framing is clearer than "user not found".
    if not x_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Username header is required",
        )

    created_at = _now_iso()
    with get_db() as conn:
        user_row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (x_username,)
        ).fetchone()
        if user_row is None:
            # A2 schema change: we do NOT auto-create the user here.
            # A1 used INSERT OR IGNORE and silently created unknowns.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user '{x_username}' not found",
            )
        cursor = conn.execute(
            "INSERT INTO posts (user_id, message, created_at) VALUES (?, ?, ?)",
            (user_row["id"], body.message, created_at),
        )
        new_id = cursor.lastrowid

    return {
        "id": new_id,
        "username": x_username,
        "message": body.message,
        "created_at": created_at,
    }


@app.get("/posts", response_model=list[PostOut])
def list_posts(
    # Query(...) attaches validation to query-string parameters.
    # FastAPI surfaces failures as 422 automatically.
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    List posts, newest first, with optional substring search.

    ?q=foo        filters to posts whose message contains "foo"
                  (case-insensitive, `%` and `_` treated literally).
    ?limit=N      returns at most N (1-200, default 50).
    ?offset=K     skips the first K (>= 0, default 0).
    """
    # The SQL below is interesting for three reasons:
    #
    # 1. We ORDER BY p.id DESC so "newest first" matches a typical
    #    feed's expectation.  limit/offset make no sense without a
    #    stable order.
    #
    # 2. For search, we use LIKE with '%' concatenation — but we
    #    ESCAPE the user's q first.  SQL LIKE treats '%' and '_'
    #    as wildcards by default; if the user searches for "50%",
    #    without escaping that becomes a prefix match on "50".
    #    The ESCAPE '\' clause tells SQLite to treat \% and \_ as
    #    literals.
    #
    # 3. LIKE is case-insensitive for ASCII in SQLite by default,
    #    which happens to match A1's search behaviour.  Good; it
    #    keeps semantics consistent between CLI and API.

    sql = """
        SELECT p.id, u.username, p.message, p.created_at
          FROM posts p
          JOIN users u ON u.id = p.user_id
    """
    params: list = []
    if q is not None:
        # Escape backslashes first (so they stay literal in SQL),
        # then the two LIKE wildcards.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += " WHERE p.message LIKE ? ESCAPE '\\' "
        params.append(f"%{escaped}%")

    sql += " ORDER BY p.id DESC LIMIT ? OFFSET ? "
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_post(r) for r in rows]


@app.get("/posts/{post_id}", response_model=PostOut)
def get_post(post_id: int):
    """One post by id.  404 if it does not exist."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT p.id, u.username, p.message, p.created_at
              FROM posts p
              JOIN users u ON u.id = p.user_id
             WHERE p.id = ?
            """,
            (post_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"post {post_id} not found",
        )
    return _row_to_post(row)


@app.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: int):
    """
    Hard-delete a post by id.

    Returns 204 (no body) on success, 404 if the id does not exist.
    We check rowcount after the DELETE instead of a SELECT-then-
    DELETE because that would be two round trips and a race.
    """
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"post {post_id} not found",
            )
    # A 204 response MUST have an empty body.  FastAPI's default is
    # to serialise `None` as `"null"`, which is NOT empty.  Returning
    # an explicit empty Response sidesteps that gotcha.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
