# BBS Webserver — Assignment 2

A FastAPI wrapper that exposes the A1 BBS over HTTP. The database layer is brand new (the A1 CLI keeps its own SQLite file in the repo root, untouched by the API). The webserver has no passwords, no boards (in bronze), and no auto-create-user-on-post — just a clean REST surface over a flat `users` and `posts` schema.

## How to run

### Dependencies

Python 3.12+ and three packages:

```
fastapi
uvicorn
httpx
```

`httpx` is only needed by `verify_api.py` and `pytest`; the server itself uses `fastapi` + `uvicorn`. Pydantic ships with FastAPI.

The repo root has a `.venv/` with everything pre-installed. If you're setting up fresh:

```bash
python3 -m venv .venv
.venv/bin/pip install -r webserver/requirements.txt
```

### Running the server

From the `webserver/` directory:

```bash
../.venv/bin/uvicorn main:app --port 8000
```

Or from the repo root:

```bash
.venv/bin/uvicorn --app-dir webserver main:app --port 8000
```

`bbs.db` is created on first startup next to `main.py`. No migration from A1 is needed — the API uses its own SQLite file.

### Running the tests

```bash
# From the repo root:
.venv/bin/python -m pytest webserver/test_api.py -v
```

85 in-process tests using FastAPI's `TestClient`. Each test gets a fresh SQLite file in a pytest `tmp_path` (see [conftest.py](conftest.py)) so the suite is order-independent and parallel-safe.

### Running the conformance verifier

Two terminals. In one:

```bash
cd webserver && ../.venv/bin/uvicorn main:app --port 8000
```

In the other:

```bash
.venv/bin/python verify_api.py
```

The script prints `PASS`/`FAIL` per check and exits non-zero if anything failed. Usernames are suffixed with a random run ID, so you don't need to wipe `bbs.db` between runs.

## Tier targeted

**Gold.**

Bronze (all 8 endpoints + three `STUDENT TODO` stubs) and silver (PATCH, bio/post_count, updated_at, `?username=` filter) are both complete. Gold adds the **boards/topics** feature:

- Posts gain a `board` field (default `"general"`).
- `GET /boards` — list every board with a post count.
- `GET /boards/{name}/posts` — list posts on a specific board.
- `POST /boards/{name}/posts` — convenience creation endpoint (URL determines board).
- `GET /posts?board=<name>` — additional composable filter, in the same shape as `?username=` and `?q=`.

`verify_api.py` now runs three layered check functions — `run_*_checks` for bronze, silver, and gold — for 86 total checks end-to-end (44 bronze + 22 silver + 20 gold).

## Design decisions

### `response_model` on every route

Every handler declares a `response_model=UserOut` or `response_model=list[PostOut]`. FastAPI filters outgoing data against the model's fields before serializing, so stray database columns never leak into responses. The naive alternative — returning `dict(row)` straight from SQLite — would quietly ship `user_id` in every post response. [`main.py:107-125`](main.py#L107-L125) defines the models; [`main.py:149`](main.py#L149) and similar lines attach them to the handlers.

### Hard delete, not soft delete

`DELETE /posts/{id}` issues an actual `DELETE FROM posts WHERE id = ?`. A soft-delete column (`deleted_at IS NULL`) would be safer for audit/recovery, but it would also mean threading "exclude deleted rows" through every single read query — search, list, user posts, get-by-id. For a bronze-tier BBS with no audit requirement, hard delete is the right trade: less code, fewer bugs, and the spec says 204 with no body, which matches the "it's gone" semantics exactly.

### Raw SQL, not an ORM

SQLAlchemy would add declarative models, sessions, connection pooling, and ~200 lines of boilerplate for a schema with two tables and seven queries. Raw SQL with `?` placeholders is safe against injection (the `sqlite3` driver binds values before they reach the engine), readable, and — importantly — mirrors the teaching from A1 so a student can trace concepts from one assignment to the next. ORMs start earning their keep at 10+ tables and complex relationships; we're nowhere near that.

### `LIKE` wildcards on search are escaped

The obvious implementation of `?q=` is `WHERE message LIKE '%' || ? || '%'`. That looks fine until you notice that SQL `LIKE`'s `%` and `_` are wildcards — so `?q=%` matches every post (effectively a no-op filter) and `?q=50_off` matches unexpected things. The handler escapes `\`, `%`, and `_` in `q` with an `ESCAPE '\'` clause so the search treats user input as a literal substring. Two tests lock this in ([`test_api.py:300-322`](test_api.py#L300-L322)).

### Envelope response for 204 DELETE

Returning `None` from a 204 handler makes FastAPI serialize it as the string `"null"`, which is a one-byte body — and 204 responses are supposed to be bodyless. [`main.py:329`](main.py#L329) returns an explicit `Response(status_code=204)` to sidestep that gotcha. One of the 85 tests asserts `r.content == b""` specifically to catch regressions here.

## Schema changes from A1

A1's schema (after our refactor) looked like this:

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT
);

CREATE TABLE posts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    board     TEXT    NOT NULL DEFAULT 'general',
    message   TEXT    NOT NULL,
    timestamp TEXT    NOT NULL
);
```

A2's schema (gold) is:

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL,
    bio         TEXT                                       -- silver, nullable
);

CREATE TABLE posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT,                                      -- silver, nullable
    board       TEXT    NOT NULL DEFAULT 'general'         -- gold
);
```

Concrete differences from A1:

- **Dropped `password_hash`** from users. The API has no notion of passwords — `X-Username` is the (non-)authentication mechanism, and real auth is out of scope.
- **Renamed `timestamp` → `created_at`** on posts to match the JSON field the spec requires in responses. Keeping them aligned saves a row → dict translation step.
- **Added `created_at`** to users so `UserOut` can report it without another table lookup.
- **Removed auto-create-user-on-post.** A1 used `INSERT OR IGNORE` on the users table when posting, silently creating unknown users. A2 treats this as an error: `POST /posts` with an unknown `X-Username` returns 404. This is a real behavior change, called out in the spec.
- **Silver: added `bio` to users and `updated_at` to posts.** Both nullable, both set by their respective `PATCH` handlers. `post_count` is NOT a column — it is computed per-request from a correlated subquery on the posts table. Storing a counter would require keeping it in sync on every post INSERT/DELETE, and drift bugs are common; computing it sidesteps the problem entirely for a sub-millisecond cost at this scale.
- **Gold: added `board` to posts.** `NOT NULL DEFAULT 'general'` — the default lives in the database (belt-and-suspenders with Pydantic's `default="general"`), so any INSERT path that omits the column still succeeds. Boards have no separate table; they exist implicitly as distinct values in `posts.board`.

## What I added to verify_api.py

### `run_delete_checks`

Instead of reusing `state["alice_post_id"]` from an earlier section (which could plausibly get deleted by a later section and break this one), I create a throw-away post inside the function, delete it, then confirm:

1. `DELETE` returned 204.
2. `GET` on the same id returns 404 (the post really is gone).
3. `DELETE` on a clearly-nonexistent id (99999999) also returns 404.

### `run_pagination_checks`

Seeds 5 posts so `limit=2` and `offset=2` both produce observable results. The interesting part is the offset check — I don't assert on any particular ordering of ids. Instead I compute the set difference between the full list's ids and the offset=2 list's ids and assert that the difference has exactly 2 elements. This is order-agnostic: whatever order the server chooses, "skipping 2" must leave behind exactly 2 ids. Then the three 422 boundary cases (`limit=0`, `limit=500`, `offset=-1`).

### `run_field_shape_checks`

Creates a fresh user and a fresh post inside the function so the assertions run against known, clean responses (no chance that an earlier section's extras have polluted them). Every assertion is **set equality**, not a subset check — an extra `email` or `updated_at` field would fail just as loudly as a missing field. Six checks cover all three shapes of each resource: `POST /users`, `GET /users/{username}`, an item from `GET /users`, and the same three paths for posts.

### Additional edge cases in the pytest suite (`test_api.py`)

These aren't in `verify_api.py` but they are worth naming — they're the tests I wrote that go beyond what the spec enumerates:

- **SQL `LIKE` wildcards**: `?q=%` and `?q=_` must return no matches, not every post — the escape clause is locked in by dedicated tests.
- **Boundary lengths**: exactly 3 and exactly 20 chars for username (min/max), exactly 1 and exactly 500 chars for message.
- **Username case sensitivity**: `alice` and `Alice` can coexist — SQLite's `UNIQUE` is case-sensitive by default, and we lock that in so a future "lowercase everything" refactor doesn't silently break existing users.
- **Non-integer path params**: `GET /posts/not-a-number` returns 422 (FastAPI's `{id:int}` coercion).
- **DELETE idempotence**: deleting twice → second is 404 (no "already-deleted" special case).
- **DELETE returns an empty body**: `r.content == b""` (catches the `Response(status_code=204)` vs `return None` issue described above).
- **`created_at` format regex**: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$`. Catches accidental microsecond drift or timezone suffix injection.
- **Unicode & newlines in messages** are preserved round-trip.
- **Empty `X-Username`** returns 400, same as missing — documented choice, tested explicitly.
- **User-with-no-posts**: `GET /users/alice/posts` returns `200 []`, not 404 (the user exists; the list is merely empty).

## X-Username and what real auth would need

`X-Username` is identity metadata, not authentication. Any client can send `X-Username: alice` and the server will accept it without verifying that the sender is, in fact, alice. The pattern introduces the shape of "HTTP requests carry identity" before we have the cryptographic machinery to verify it.

Real authentication would need to add a **secret the client holds and the server can verify**. The standard moves:

1. **Registration** issues the user a credential. In the simplest form this is a password, which the server stores only as a salted hash (A1's CLI already does this — PBKDF2-SHA256, constant-time compare). In production-grade systems this is often an OAuth flow through an identity provider.
2. **Login** trades the credential for a short-lived proof of identity — a session cookie, a JWT, or an opaque bearer token — scoped to a specific user. The credential itself stops traveling on each request.
3. **Every authenticated request** carries that proof in a header like `Authorization: Bearer <token>`. The server verifies the token's signature (or looks it up in a session store) on each request and only then trusts the claimed identity. A middleware / FastAPI dependency would run this check before any handler sees the request.
4. **HTTPS** is mandatory, because the token is only a secret while nobody else sees it.
5. **Tokens expire and rotate**, so a stolen one stops working before too much damage is done. Refresh tokens handle the reissue.

None of this changes the *shape* of the API — `POST /posts` still takes a body and returns a 201. What changes is the header: `X-Username: alice` becomes `Authorization: Bearer eyJ…`, and the `X-Username` value on the resulting resource is derived from the verified token, not copied out of a trusted header. That's the move, in one sentence: stop trusting the header, start verifying a signed credential.

## Silver additions

### `bio` and `post_count` on user responses

Every user response (POST /users, GET /users, GET /users/{username}, PATCH /users/{username}) now carries two extra fields:

- **`bio`** — a nullable string (max 200 chars). Null for users who have never set one. Set via `PATCH /users/{username}`.
- **`post_count`** — an integer, always present. Computed from a correlated subquery `(SELECT COUNT(*) FROM posts WHERE p.user_id = u.id)` on every read, never stored.

The same SELECT fragment ([`main.py:139-146`](main.py#L139-L146)) serves POST, GET one, GET list, and PATCH. Centralizing it means a future schema change only touches one string.

### `updated_at` on post responses

Post responses now include `updated_at`. It is `null` for posts that have never been edited, and becomes an ISO-seconds timestamp the first (and every subsequent) time `PATCH /posts/{id}` successfully changes the message. A no-op PATCH (empty body) leaves `updated_at` alone rather than bumping it — mutating history on a no-op would be a lie.

### `PATCH /users/{username}` — edit bio

Body schema: `{"bio": "..."}` or `{"bio": null}` or `{}`.

- `{"bio": "text"}` → sets bio, returns 200 with the full silver user shape.
- `{"bio": null}` → clears bio back to null.
- `{}` → no-op, returns 200 with current state.
- Unknown keys (e.g. a forward-compat `"avatar"`) are silently ignored (Pydantic v2's default is `extra='ignore'`), so a client shipping a future field does not break on today's server.
- Missing user → 404. Bio > 200 chars → 422.

The trick for distinguishing "field absent" from "field present with null" in PATCH is `model_dump(exclude_unset=True)` — if `"bio"` is in the returned dict, the client explicitly sent it (regardless of value); if not, the field was omitted.

### `PATCH /posts/{id}` — edit message, with ownership enforcement

**Ownership policy chosen: A — `X-Username` must match the post's author, or 403.**

The spec gave two defensible options (enforce match, or allow anyone since `X-Username` isn't real auth). I picked the enforced-match option for two reasons:

1. It gets the *concept* of ownership into the API today. When real auth arrives in a later lecture, the only change is swapping the `X-Username` string-comparison for a verified-token lookup. The 403 code stays, the endpoint shape stays, the tests stay.
2. "Allow anyone" would make the endpoint uninteresting to test. Silver is a place to grow the surface area, not shave it.

Status-code order on `PATCH /posts/{id}` (the order matters):

| Situation | Code |
|---|---|
| `X-Username` header missing or empty | 400 |
| Post id does not exist | 404 |
| Post exists, but `X-Username` does not match the stored author | 403 |
| Message body fails validation (empty, >500 chars, explicit null) | 422 |
| OK | 200 |

The 404-before-403 ordering is the plain-REST default: an unknown post id always looks the same regardless of who is asking. A security-hardened build would flip this to 403-everywhere to avoid confirming whether a given id exists. Silver is not worried about that threat model.

A subtle Pydantic point: `min_length=1` on an `Optional[str]` field does NOT reject `None` — the constraint only fires when the value is a string. A body of `{"message": null}` would silently pass. The handler catches this explicitly with a `HTTP_422_UNPROCESSABLE_CONTENT` before the DB write ([`main.py:452-457`](main.py#L452-L457)).

### `GET /posts?username=alice` — filter by author

Adds a `?username=<name>` query parameter that composes with the existing `?q=`, `?limit=`, and `?offset=` parameters. The filter is an equality match in the WHERE clause — not a JOIN re-shape, just one more `AND u.username = ?` wired into the same SQL builder.

Why **filter semantics, not lookup semantics** for unknown usernames: `GET /posts?username=ghost` returns `200 []`, not 404. Contrast with `GET /users/{username}/posts` (path parameter) where a missing user is 404 — there, the username is *part of the resource identifier*. Here, it is a *filter*, consistent with `?q=nomatch` returning `[]` rather than 404. Mixing the two semantics would be confusing and inconsistent.

### What I added to verify_api.py for silver

- Updated the two bronze exact-set assertions (`POST /users` and `POST /posts` response shapes) to the silver 4-field and 5-field sets.
- Updated `run_field_shape_checks` expected sets to the silver shapes.
- New `run_silver_checks` function, called from `main()` after the bronze section. It exercises (against a live server):
  - Fresh user has `bio=null` and `post_count=0`.
  - `post_count` reacts to `POST /posts` (becomes 2 after two posts).
  - `PATCH /users/{username}` happy path: 200, bio echoed, silver user shape.
  - `PATCH /users` with a 201-char bio: 422.
  - `PATCH /users` with `{"bio": null}`: clears the stored bio.
  - `PATCH /users/{ghost}`: 404.
  - `PATCH /posts/{id}` owner happy path: 200, message updated, `updated_at` is not null, silver post shape.
  - `PATCH /posts/{id}` without `X-Username`: 400.
  - `PATCH /posts/{id}` with a different user's header: 403 (the ownership policy test).
  - `PATCH /posts/99999999`: 404.
  - `GET /posts?username=<user>` returns only that user's posts.
  - `GET /posts?username=<user-with-no-posts>`: 200 `[]`.
  - `GET /posts?username=<ghost>`: 200 `[]` (filter semantics, not 404).

### What I added to the pytest suite for silver

On top of what `verify_api.py` checks, `test_api.py` adds ~50 silver-specific tests. The ones that catch non-obvious bugs:

- **`post_count` decrements on DELETE** — guards against a future refactor that caches the count somewhere and goes stale after a delete.
- **Silver list-users has `post_count=0` for users with no posts** — catches a LEFT JOIN that's secretly an INNER JOIN and drops the no-post users. (We use a correlated subquery, which has no such pitfall — but if the implementation ever switches to `GROUP BY`, this test fires first.)
- **`PATCH /users` with empty string bio** is accepted and distinct from `null` — a "user cleared their bio text without nulling the field" case that is easy to conflate.
- **`PATCH /users` ignores unknown fields** (e.g. `{"username": "mallory"}` in the body) — forward-compat guard.
- **`PATCH /users` 422 on non-string bio** — Pydantic type check.
- **`PATCH /posts` with `{"message": null}` is 422** — distinct from `{}` (no-op 200). The Pydantic null-vs-missing subtlety described above.
- **403 on wrong author actually refuses the write** (defense in depth: a handler that returned 403 after writing would be worse than one that returned 200).
- **Empty-body PATCH does not set `updated_at`** — no-op must not lie about resource history.
- **404 beats 403** for a nonexistent post with a wrong-author header.
- **`?username=` filter composes with `?q=` AND `?limit=` AND `?offset=`** — all three in one test, confirming the WHERE / LIMIT / OFFSET apply in the right order.

## Gold additions — Boards / topics

### The feature

Every post now belongs to a named board. Boards exist **implicitly**: a board is real if and only if at least one post references it. There is no `boards` table, no `POST /boards`, no way to reserve an empty board. Adding an empty-board registry is straightforward if it's ever needed (add a `boards` table, `UNION` its rows into `GET /boards`), but the spec doesn't require it and the minimal design pays off in less surface area to test and reason about.

### Three new endpoints + one new filter

| Endpoint | Method | Purpose |
|---|---|---|
| `/boards` | GET | List every board with at least one post, with post counts. |
| `/boards/{name}/posts` | GET | Posts on one board (empty array for never-posted-to boards — filter semantics, not lookup). |
| `/boards/{name}/posts` | POST | Convenience creation — URL determines the board; body only carries `{"message": "..."}`. |
| `/posts?board=<name>` | GET | Additional composable filter on the existing list endpoint. |

### Why implicit boards (design decision)

- **Simpler schema.** One column, no second table, no referential integrity on "board must exist before posting." Posting to a new board creates it in the same stroke.
- **Matches the A1 CLI (post-refactor).** After we flattened A1's table-per-board schema in an earlier review round, the CLI also uses one column and computes boards via `SELECT DISTINCT board`. Keeping the two layers architecturally aligned means future features transfer cleanly between them.
- **Easy to evolve.** If an empty-boards registry is later needed (for description, privacy flags, ownership, etc.), a `boards` table can be added with a backfill of `SELECT DISTINCT board FROM posts` and the `GET /boards` query switches to `UNION` the two sources. None of the public-facing semantics change.

### Why `POST /boards/{name}/posts` does not accept `board` in the body

Having both `URL.{name}` and `body.board` forces a conflict-resolution decision (which wins? silent override? 409?). The cleanest move is to make the body schema physically incapable of carrying a `board` field — so the URL is authoritative *by construction*, not by policy. `BoardPostCreate` is a two-line Pydantic model with only `message`; the URL-derived name flows through the same `_insert_post()` helper that `POST /posts` uses, so both endpoints share one write path and cannot drift.

### Pydantic pattern: `str` with `Field(default=...)` vs silver's `Optional[str]`

Silver's `bio: Optional[str] = None` lets clients send `{"bio": null}` to mean "clear". Gold's `board: str = Field(default="general", ...)` is deliberately different:

| Client sends | Silver `bio` | Gold `board` |
|---|---|---|
| field omitted | left unchanged (PATCH) | default applied (`"general"`) |
| string value | validated & used | validated & used |
| `null` | valid — clears to NULL | **422** (type mismatch — `str` field can't be null) |

That asymmetry is intentional. A post without a board is nonsensical (every post needs a home), so we make it structurally impossible to send one.

### Board name validation

`^[a-zA-Z0-9_]+$`, max 32 chars. Same rules as the A1 CLI's `validate_board()` helper. Enforced in two places:

- On the body (`POST /posts`), via `Field(pattern=..., max_length=32)` on the Pydantic model.
- On the URL (`/boards/{name}/posts`), via `Path(pattern=..., max_length=32)` — the exact same regex, this time on the path parameter. A malformed URL returns 422 before any handler code runs.

### What I added to verify_api.py for gold

- Updated the `POST /posts` shape assertion in `run_post_checks` from 5 fields to 6 (added `board`).
- Updated `run_field_shape_checks` expected post set from 5 to 6.
- Renamed one historical silver-era assertion from "silver post shape" to "current post shape" and pointed it at the gold 6-field set — tier-bump hygiene.
- New `run_gold_checks` function (20 end-to-end checks), called from `main()` after `run_silver_checks`. It verifies:
  - POST /posts with no board → `board == "general"`.
  - POST /posts with an explicit board → lands on that board.
  - POST /posts with an invalid board name / `null` board → 422.
  - GET /boards returns a 200 JSON array.
  - GET /boards items have exactly `{name, post_count}`.
  - GET /boards counts are correct for a seeded mix of boards.
  - GET /boards/{existing}/posts filters to that board.
  - GET /boards/{unposted}/posts returns 200 `[]` (filter semantics).
  - GET /boards/{malformed}/posts returns 422 (Path regex).
  - POST /boards/{name}/posts: 201, response has URL's board, gold shape.
  - POST /boards/{name}/posts: 400 without X-Username, 404 with unknown user.
  - GET /posts?board=X filters, and composes with `username` + `q`.

### What I added to the pytest suite for gold

Roughly 40 gold tests. Non-obvious bugs locked in:

- **PATCH preserves board.** A future refactor that accidentally writes `UPDATE posts SET board = ?` based on a missing-but-defaulting Pydantic field would wipe the board. Test catches that.
- **Deleting the last post in a board makes the board disappear from GET /boards.** No-row → no-group is correct behavior; the test pins that we don't accidentally cache a stale board list.
- **POST /boards/{name}/posts via the convenience endpoint shows up in GET /boards.** End-to-end round-trip check that the two write paths touch the same storage.
- **URL path validation mirrors body validation** — `/boards/has spaces/posts` returns 422, not a silent acceptance.
- **`?board=ghost` returns 200 `[]`**, not 404 — same filter-vs-lookup distinction used for `?username=` in silver. Consistency across the API matters.
- **All four filters compose at once** — `board`, `username`, `q`, and `limit` in a single test. If any of them drop out of the SQL builder, the test fires immediately.
