"""
main.py — FastAPI BBS webserver (Assignment 2, silver tier).

READING GUIDE
─────────────
This file has four layers, top to bottom:

  1. Lifespan + app setup    — runs init_db() on startup.
  2. Pydantic models         — request validation (UserCreate,
                               PostCreate, UserPatch, PostPatch) and
                               response shape lockdown (UserOut, PostOut).
  3. Row → dict helpers + SQL fragments kept in one place.
  4. Route handlers, grouped by resource.  Raw SQL (no ORM) because
     the schema is small enough that ORM machinery would obscure
     the logic.

WHY PYDANTIC MODELS FOR BOTH REQUESTS AND RESPONSES
────────────────────────────────────────────────────
The spec says response bodies must contain EXACTLY the listed fields.
The naive implementation — returning dict(row) straight from SQLite —
leaks internal columns like user_id.  By declaring response_model on
each route, FastAPI filters the outgoing dict against the model's
fields before serialising.  Any stray field silently drops; any
missing field raises a server error that surfaces in tests.  Field-
shape correctness comes for free.

WHY RAW SQL INSTEAD OF AN ORM
──────────────────────────────
SQLAlchemy would add 200+ lines of boilerplate for a schema with two
tables and a handful of queries.  Raw SQL parameterised with `?` is
safe against injection (the sqlite3 driver binds values properly),
readable, and mirrors A1's teaching so students can trace from one
assignment to the next.  Silver's correlated subquery for post_count
is three lines of SQL; hiding it inside an ORM relationship would
just cost an extra query per user.

SILVER IN ONE PARAGRAPH
───────────────────────
Silver expands user responses with `bio` (nullable, editable via
PATCH) and `post_count` (always computed from the posts table, never
stored).  Post responses gain `updated_at` (null until the first
PATCH).  Two new handlers: PATCH /users/{username} sets bio; PATCH
/posts/{id} sets message and bumps updated_at.  GET /posts gains a
`?username=` filter that composes with `?q=`, `?limit=`, and
`?offset=`.  The PATCH on posts enforces an ownership policy —
the X-Username header must match the post's author, or we return
403.  That's "ownership option A" from the spec; the rationale is
in the README.
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
# code before `yield` runs on startup, code after runs on shutdown.
# TestClient(app) as a context manager triggers the same lifespan —
# that is how every per-test tmp DB gets its schema created.
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="BBS Webserver", lifespan=lifespan)


# ══════════════════════════════════════════════════════════════════════
#  Pydantic models
# ══════════════════════════════════════════════════════════════════════
#
# Incoming models validate request bodies.  Outgoing models filter
# the response shape.  Keep them in one place so the spec changes
# feel self-contained.

class UserCreate(BaseModel):
    """Body for POST /users."""
    # Field(..., pattern=...) rejects anything outside the spec's
    # character class.  The trailing "..." sentinel means "required"
    # (Pydantic v2 idiom).
    username: str = Field(..., min_length=3, max_length=20,
                          pattern=r"^[a-zA-Z0-9_]+$")


class PostCreate(BaseModel):
    """Body for POST /posts."""
    message: str = Field(..., min_length=1, max_length=500)


class UserPatch(BaseModel):
    """
    Body for PATCH /users/{username}.

    Only one field today (bio).  The type is `str | None`, the
    default is None, and the field is optional — three things that
    together give us the three PATCH behaviours we need:

      body {"bio": "hi"}   → update bio to "hi"
      body {"bio": null}   → clear bio back to null
      body {}              → no-op (field is absent from model_dump(exclude_unset=True))

    Unknown keys silently drop (Pydantic v2 defaults to extra='ignore'),
    so a forward-looking client that ships a future "avatar" field
    does not break on today's server.
    """
    bio: Optional[str] = Field(default=None, max_length=200)


class PostPatch(BaseModel):
    """
    Body for PATCH /posts/{id}.

    Same three-behaviour pattern as UserPatch, except:

      body {"message": "new"}  → update message
      body {"message": null}   → 422 (handled in the handler — a
                                  null message is nonsensical; the
                                  validator can't tell null-set from
                                  null-unset at this layer)
      body {}                  → no-op, updated_at left untouched

    min_length=1 here means "if message IS a string, it must be non-empty".
    It does not reject None — that check happens in the handler.
    """
    message: Optional[str] = Field(default=None, min_length=1, max_length=500)


class UserOut(BaseModel):
    """
    Silver user response.  Exactly four fields — enforced by
    response_model on every /users endpoint.
    """
    username: str
    created_at: str
    bio: Optional[str] = None
    post_count: int


class PostOut(BaseModel):
    """
    Silver post response.  Exactly five fields.  updated_at is null
    for posts that have never been PATCHed.
    """
    id: int
    username: str
    message: str
    created_at: str
    updated_at: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════
#  SQL fragments, time helpers, row adapters
# ══════════════════════════════════════════════════════════════════════
#
# The user SELECT is used by POST, GET one, GET list, and PATCH —
# four places.  Pulling it into a constant means a single change to
# the silver shape does not require touching four query strings.

_USER_SELECT = """
SELECT u.username,
       u.created_at,
       u.bio,
       (SELECT COUNT(*) FROM posts p WHERE p.user_id = u.id) AS post_count
  FROM users u
"""

_POST_SELECT = """
SELECT p.id,
       u.username,
       p.message,
       p.created_at,
       p.updated_at
  FROM posts p
  JOIN users u ON u.id = p.user_id
"""


def _now_iso() -> str:
    """
    Current UTC time in the spec's format: YYYY-MM-DDTHH:MM:SS.

    Strip tzinfo so isoformat() does not append "+00:00".
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _row_to_user(row: sqlite3.Row) -> dict:
    """
    Turn a users-with-post_count row into a UserOut-shaped dict.

    Uses dict-literal construction (not dict(row)) because dict(row)
    would include the internal `id` column.  Explicit is safer.
    """
    return {
        "username":   row["username"],
        "created_at": row["created_at"],
        "bio":        row["bio"],
        "post_count": row["post_count"],
    }


def _row_to_post(row: sqlite3.Row) -> dict:
    """Turn a posts-join-users row into a PostOut-shaped dict."""
    return {
        "id":         row["id"],
        "username":   row["username"],
        "message":    row["message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _fetch_user(conn, username: str):
    """Load one user in full silver shape, or None if missing."""
    return conn.execute(
        _USER_SELECT + " WHERE u.username = ?",
        (username,),
    ).fetchone()


def _fetch_post(conn, post_id: int):
    """Load one post in full silver shape, or None if missing."""
    return conn.execute(
        _POST_SELECT + " WHERE p.id = ?",
        (post_id,),
    ).fetchone()


# ══════════════════════════════════════════════════════════════════════
#  /users
# ══════════════════════════════════════════════════════════════════════

@app.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def create_user(body: UserCreate):
    """
    Create a new user.

    201 on success, 409 on duplicate username, 422 on validation
    failure (handled by Pydantic before we get here).  A freshly-
    created user has bio=null and post_count=0 by construction —
    no DB round trip needed for that.
    """
    created_at = _now_iso()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, created_at) VALUES (?, ?)",
                (body.username, created_at),
            )
    except sqlite3.IntegrityError:
        # UNIQUE constraint on username.  That is the only
        # IntegrityError we expect here; any other is a real bug.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"username '{body.username}' already exists",
        )
    return {
        "username": body.username,
        "created_at": created_at,
        "bio": None,
        "post_count": 0,
    }


@app.get("/users", response_model=list[UserOut])
def list_users():
    """List every user in the silver shape.  [] when empty (not 404)."""
    with get_db() as conn:
        rows = conn.execute(_USER_SELECT + " ORDER BY u.id").fetchall()
    return [_row_to_user(r) for r in rows]


@app.get("/users/{username}", response_model=UserOut)
def get_user(username: str):
    """Look up one user.  404 if missing."""
    with get_db() as conn:
        row = _fetch_user(conn, username)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user '{username}' not found",
        )
    return _row_to_user(row)


@app.patch("/users/{username}", response_model=UserOut)
def update_user(username: str, body: UserPatch):
    """
    PATCH semantics:
      - {"bio": "x"} → sets bio to "x"
      - {"bio": null} → clears bio (sets to NULL)
      - {} → no-op; returns current state with 200

    Unknown fields in the body are ignored (Pydantic's default).

    We always re-read and return the full silver shape so the
    response reflects the saved state, not the request.  That makes
    the endpoint safe to retry and gives the client post_count too.
    """
    # model_dump(exclude_unset=True) is the one incantation that
    # distinguishes "field absent" from "field present with value
    # None".  Any field in `updates` was explicitly sent by the
    # client; missing fields stay absent.
    updates = body.model_dump(exclude_unset=True)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user '{username}' not found",
            )
        if "bio" in updates:
            conn.execute(
                "UPDATE users SET bio = ? WHERE id = ?",
                (updates["bio"], existing["id"]),
            )
        row = _fetch_user(conn, username)
    return _row_to_user(row)


@app.get("/users/{username}/posts", response_model=list[PostOut])
def list_user_posts(username: str):
    """
    All posts by one user.

    Distinguish "user does not exist" (404) from "user exists, no
    posts" (200 []) with a cheap existence check before the join.
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
            _POST_SELECT + " WHERE u.username = ? ORDER BY p.id",
            (username,),
        ).fetchall()
    return [_row_to_post(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════
#  /posts
# ══════════════════════════════════════════════════════════════════════

@app.post("/posts", status_code=status.HTTP_201_CREATED, response_model=PostOut)
def create_post(
    body: PostCreate,
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
):
    """
    Create a post on behalf of X-Username.

    400 — header missing or empty
    404 — header names an unknown user
    422 — message validation fails (Pydantic)
    201 — OK; response updated_at is null (nothing's been edited yet)
    """
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
        "updated_at": None,
    }


@app.get("/posts", response_model=list[PostOut])
def list_posts(
    # Query(...) attaches validation to query-string parameters.
    # FastAPI surfaces failures as 422 automatically.
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    # Silver: filter by author.  No pattern= here because the
    # filter semantics for an unknown/malformed username is "return
    # [] (no matches)", not "422 because your filter string has a
    # dash".  Cleaner to let any string flow through.
    username: Optional[str] = Query(default=None),
):
    """
    List posts, newest first, with optional substring search and
    optional author filter.

    ?q=foo         — message contains "foo" (literal, LIKE wildcards
                     escaped; case-insensitive via SQLite's default
                     LIKE behaviour).
    ?username=X    — posts by author X only (silver).  Unknown or
                     malformed X → [].  Composes with all other
                     filters (intersection).
    ?limit=N       — cap results at N (1-200, default 50).
    ?offset=K      — skip first K (>= 0, default 0).
    """
    # Build the query out of a base SELECT + an AND-joined list of
    # WHERE clauses.  Keeping params in lock-step with the clauses
    # makes it easy to add more filters later (gold tier: ?board=).
    sql = _POST_SELECT
    where: list[str] = []
    params: list = []

    if q is not None:
        # SQL LIKE: '%' and '_' are wildcards.  If the user searches
        # for "50%", we want a literal-substring match, not a prefix
        # match on "50".  Escape backslash first (so it stays literal
        # in the SQL text), then the two LIKE wildcards.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("p.message LIKE ? ESCAPE '\\' ")
        params.append(f"%{escaped}%")

    if username is not None:
        # Equality filter.  Exact match, case-sensitive — consistent
        # with how usernames are stored and compared everywhere else.
        where.append("u.username = ?")
        params.append(username)

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY p.id DESC LIMIT ? OFFSET ? "
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_post(r) for r in rows]


@app.get("/posts/{post_id}", response_model=PostOut)
def get_post(post_id: int):
    """One post by id.  404 if missing."""
    with get_db() as conn:
        row = _fetch_post(conn, post_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"post {post_id} not found",
        )
    return _row_to_post(row)


@app.patch("/posts/{post_id}", response_model=PostOut)
def update_post(
    post_id: int,
    body: PostPatch,
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
):
    """
    Edit a post's message.  Ownership is enforced — only the post's
    original author (as stored on the row) can edit it, and that
    identity is claimed via the X-Username header.

    Order of checks (matters for the response code):
      400 — X-Username missing or empty
      404 — post does not exist
      403 — post exists but X-Username does not match the author
      422 — body fails validation (Pydantic) OR explicit null message
      200 — OK (includes {} no-op case, which leaves updated_at alone)

    The 404-before-403 ordering means an unknown post id always
    looks the same to any caller, regardless of whether they own it.
    That is the plain-REST default; a security-hardened build might
    flip this to 403-everywhere to avoid leaking whether an id
    exists, but bronze is not worried about that.
    """
    if not x_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Username header is required",
        )

    updates = body.model_dump(exclude_unset=True)
    # Reject an explicit null message here rather than at validation
    # time, because Pydantic's min_length=1 constraint does not fire
    # on None (the value short-circuits the string check).  We want
    # null to count as a 422, so we raise one manually.
    if "message" in updates and updates["message"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="message cannot be null",
        )

    with get_db() as conn:
        row = conn.execute(
            "SELECT p.id, u.username "
            "  FROM posts p "
            "  JOIN users u ON u.id = p.user_id "
            " WHERE p.id = ?",
            (post_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"post {post_id} not found",
            )
        if row["username"] != x_username:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="you can only edit your own posts",
            )

        if "message" in updates:
            # Only touch updated_at when we actually changed
            # something.  A no-op PATCH ({}) leaves updated_at alone.
            conn.execute(
                "UPDATE posts SET message = ?, updated_at = ? WHERE id = ?",
                (updates["message"], _now_iso(), post_id),
            )

        post_row = _fetch_post(conn, post_id)
    return _row_to_post(post_row)


@app.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: int):
    """
    Hard-delete a post.  204 on success, 404 if missing.

    Check rowcount after the DELETE instead of SELECT-then-DELETE:
    one round trip, no race.
    """
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"post {post_id} not found",
            )
    # 204 must have an empty body.  FastAPI would serialise `None`
    # as the string "null", which is a byte of body.  Returning an
    # explicit empty Response guarantees zero bytes.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
