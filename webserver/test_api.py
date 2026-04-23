"""
test_api.py — spec-driven tests for the BBS webserver.

HOW TO READ THIS FILE
─────────────────────
Every test maps to a concrete behaviour listed in Assignment 2:
  - response shapes (exact key sets — no stray fields)
  - status codes for happy/sad paths
  - validation rules on usernames, messages, limit, offset
  - the X-Username header behaviour
  - search, pagination, and delete semantics

The tests use FastAPI's TestClient, which runs the app in-process.  That
means no uvicorn, no port, no network — the assertions are fast and do
not race with a live server.  Each test receives a `client` fixture
that is backed by a fresh SQLite file (see conftest.py), so ordering
never matters.

Tests are grouped into sections by the concern they exercise.  Within
each section, the first few tests check the happy path, then we walk
the edge cases one by one.  This layout is deliberate — when a test
fails, the order of failures usually tells you where in the spec a
regression landed.
"""

import re
import pytest


# ──────────────────────────────────────────────────────────────────────
#  Shape helpers
# ──────────────────────────────────────────────────────────────────────
#
# A2 is very strict about what fields appear in a response.  A user
# object must have EXACTLY these fields — not a subset, not a
# superset.  These two sets are the source of truth for that check.
# The verifier's STUDENT TODO #3 compares set(body.keys()) against
# these, and so do we.
#
# Tier progression (each row is a superset of the row above):
#   bronze:  users = {username, created_at}
#            posts = {id, username, message, created_at}
#   silver:  users += {bio, post_count}   (bio nullable, post_count computed)
#            posts += {updated_at}        (nullable; set by PATCH)
#   gold:    users unchanged
#            posts += {board}             (NOT NULL, defaults to 'general')
#
# The whole point of reading from these two constants is that a tier
# bump is a one-line change.  Every existing field-shape test now
# asserts the gold shape automatically.
USER_FIELDS = {"username", "created_at", "bio", "post_count"}
POST_FIELDS = {"id", "username", "message", "created_at", "updated_at", "board"}
BOARD_FIELDS = {"name", "post_count"}

# created_at is documented as ISO-8601 with second precision and no
# timezone — e.g. "2026-04-13T14:01:32".  A regex is enough to catch
# accidental format drift (microseconds, +00:00, space instead of T).
ISO_SECONDS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


# ══════════════════════════════════════════════════════════════════════
#  POST /users
# ══════════════════════════════════════════════════════════════════════

def test_create_user_returns_201(client):
    # Happy path: the spec says successful create is 201, not 200.
    r = client.post("/users", json={"username": "alice"})
    assert r.status_code == 201


def test_create_user_response_has_exactly_user_fields(client):
    # If we accidentally return the SQL row dict (with `id`,
    # `password_hash`, etc.), this assertion catches the leak.
    r = client.post("/users", json={"username": "alice"})
    assert set(r.json().keys()) == USER_FIELDS


def test_create_user_response_echoes_username(client):
    r = client.post("/users", json={"username": "alice"})
    assert r.json()["username"] == "alice"


def test_create_user_response_has_iso_timestamp(client):
    # Format drift is easy to ship accidentally (e.g. switching to
    # isoformat() without timespec="seconds" suddenly adds microseconds).
    r = client.post("/users", json={"username": "alice"})
    assert ISO_SECONDS_RE.match(r.json()["created_at"]), r.json()


def test_create_user_duplicate_returns_409(client, alice):
    # Spec: POST /users with a username that already exists is 409.
    r = client.post("/users", json={"username": "alice"})
    assert r.status_code == 409


def test_create_user_missing_body_returns_422(client):
    # Pydantic turns "no JSON body at all" into 422.
    r = client.post("/users")
    assert r.status_code == 422


def test_create_user_missing_username_field_returns_422(client):
    r = client.post("/users", json={})
    assert r.status_code == 422


def test_create_user_empty_string_returns_422(client):
    # min_length=3 catches this.
    r = client.post("/users", json={"username": ""})
    assert r.status_code == 422


def test_create_user_too_short_returns_422(client):
    # Exactly 2 chars — one below the minimum.
    r = client.post("/users", json={"username": "ab"})
    assert r.status_code == 422


def test_create_user_at_min_length_passes(client):
    # Boundary: exactly 3 chars should be accepted.
    r = client.post("/users", json={"username": "abc"})
    assert r.status_code == 201


def test_create_user_at_max_length_passes(client):
    # Boundary: exactly 20 chars should be accepted.
    r = client.post("/users", json={"username": "a" * 20})
    assert r.status_code == 201


def test_create_user_too_long_returns_422(client):
    # 21 chars — one over the maximum.
    r = client.post("/users", json={"username": "a" * 21})
    assert r.status_code == 422


@pytest.mark.parametrize("bad", [
    "has spaces",   # space — common copy/paste bug
    "alice!",       # punctuation
    "alice-bob",    # hyphen — looks identifier-ish but isn't in the regex
    "alice.bob",    # dot
    "aliçe",        # non-ASCII letter
    "alice\n",      # trailing newline — sneaks past naive length checks
])
def test_create_user_invalid_chars_return_422(client, bad):
    # The regex is ^[a-zA-Z0-9_]+$, so all of the above must fail.
    r = client.post("/users", json={"username": bad})
    assert r.status_code == 422, f"{bad!r} was accepted"


@pytest.mark.parametrize("good", [
    "alice",
    "ALICE",
    "Alice99",
    "123",         # purely numeric — the regex allows this
    "user_name",   # underscore is allowed
    "___",         # only underscores — still matches, still length 3
])
def test_create_user_valid_formats_pass(client, good):
    r = client.post("/users", json={"username": good})
    assert r.status_code == 201, f"{good!r} was rejected"


def test_usernames_are_case_sensitive(client):
    # The spec does not mandate case-folding.  SQLite's UNIQUE column
    # uses binary comparison by default, so "alice" and "Alice" should
    # coexist.  Locking this in prevents a future "let's lowercase
    # everything" refactor from silently breaking existing users.
    a = client.post("/users", json={"username": "alice"})
    b = client.post("/users", json={"username": "Alice"})
    assert a.status_code == 201
    assert b.status_code == 201


# ══════════════════════════════════════════════════════════════════════
#  GET /users, GET /users/{username}
# ══════════════════════════════════════════════════════════════════════

def test_get_users_returns_200(client):
    r = client.get("/users")
    assert r.status_code == 200


def test_get_users_empty_returns_empty_array(client):
    # A fresh DB has no users.  Spec says list endpoints return a bare
    # JSON array — so we expect [], not null, not {"users": []}.
    r = client.get("/users")
    assert r.json() == []


def test_get_users_returns_bare_array(client, alice, bob):
    # Envelope objects like {"data": [...]} are explicitly banned in
    # bronze ("No envelope objects in bronze").
    body = client.get("/users").json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_get_users_items_have_exact_user_fields(client, alice):
    # Protects against leaking `id` or `password_hash` in list items.
    items = client.get("/users").json()
    assert set(items[0].keys()) == USER_FIELDS


def test_get_user_by_username_200(client, alice):
    r = client.get("/users/alice")
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_get_user_by_username_has_exact_fields(client, alice):
    body = client.get("/users/alice").json()
    assert set(body.keys()) == USER_FIELDS


def test_get_user_by_username_404_when_missing(client):
    r = client.get("/users/nonexistent")
    assert r.status_code == 404


def test_get_user_404_body_uses_detail_key(client):
    # Spec: use FastAPI's default error shape, {"detail": "..."}.
    r = client.get("/users/nonexistent")
    assert "detail" in r.json()


# ══════════════════════════════════════════════════════════════════════
#  POST /posts  (with the X-Username header dance)
# ══════════════════════════════════════════════════════════════════════

def test_create_post_with_valid_header_returns_201(client, alice):
    r = client.post("/posts",
                    json={"message": "hello"},
                    headers={"X-Username": "alice"})
    assert r.status_code == 201


def test_create_post_response_has_exact_post_fields(client, alice):
    # The easy field-leak bug in a raw-SQL handler is to dict(row)
    # and return the whole users-join — `user_id` sneaks in.  This
    # assertion catches that.
    r = client.post("/posts",
                    json={"message": "hello"},
                    headers={"X-Username": "alice"})
    assert set(r.json().keys()) == POST_FIELDS


def test_create_post_response_matches_input(client, alice):
    r = client.post("/posts",
                    json={"message": "hello"},
                    headers={"X-Username": "alice"})
    body = r.json()
    assert body["username"] == "alice"
    assert body["message"] == "hello"
    assert isinstance(body["id"], int)
    assert ISO_SECONDS_RE.match(body["created_at"])


def test_create_post_without_x_username_returns_400(client, alice):
    # Spec: missing X-Username is 400, not 422 (that's Pydantic's
    # territory — the header check is custom code).
    r = client.post("/posts", json={"message": "hello"})
    assert r.status_code == 400


def test_create_post_empty_x_username_returns_400(client, alice):
    # Empty string header is treated the same as missing.  This is
    # our project's choice — the spec does not mandate it — and we
    # prefer 400 over 404 because an empty header is a client bug,
    # not a "user not found" situation.
    r = client.post("/posts",
                    json={"message": "hello"},
                    headers={"X-Username": ""})
    assert r.status_code == 400


def test_create_post_unknown_user_returns_404(client):
    # Important: this is the A1 → A2 schema change.  A1 auto-created
    # the user.  A2 must return 404.
    r = client.post("/posts",
                    json={"message": "hi"},
                    headers={"X-Username": "ghost"})
    assert r.status_code == 404


def test_create_post_missing_message_returns_422(client, alice):
    r = client.post("/posts", json={}, headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_create_post_empty_message_returns_422(client, alice):
    r = client.post("/posts",
                    json={"message": ""},
                    headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_create_post_max_length_message_passes(client, alice):
    # Exactly 500 chars — boundary.
    r = client.post("/posts",
                    json={"message": "x" * 500},
                    headers={"X-Username": "alice"})
    assert r.status_code == 201


def test_create_post_too_long_message_returns_422(client, alice):
    # 501 chars — one over the boundary.
    r = client.post("/posts",
                    json={"message": "x" * 501},
                    headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_create_post_preserves_newlines(client, alice):
    # JSON lets message contain \n — we should store and return it
    # verbatim.
    msg = "line one\nline two"
    r = client.post("/posts",
                    json={"message": msg},
                    headers={"X-Username": "alice"})
    assert r.status_code == 201
    assert r.json()["message"] == msg


def test_create_post_preserves_unicode(client, alice):
    msg = "héllo 🌮 世界"
    r = client.post("/posts",
                    json={"message": msg},
                    headers={"X-Username": "alice"})
    assert r.status_code == 201
    assert r.json()["message"] == msg


def test_sequential_posts_have_strictly_increasing_ids(client, alice):
    # Guard against a weird implementation that reuses ids or returns
    # rowids out of order.
    ids = []
    for i in range(3):
        r = client.post("/posts",
                        json={"message": f"m{i}"},
                        headers={"X-Username": "alice"})
        ids.append(r.json()["id"])
    assert ids == sorted(set(ids))
    assert len(ids) == 3


# ══════════════════════════════════════════════════════════════════════
#  GET /posts, GET /posts/{id}, GET /users/{username}/posts
# ══════════════════════════════════════════════════════════════════════

def test_get_posts_empty_returns_empty_array(client):
    r = client.get("/posts")
    assert r.status_code == 200
    assert r.json() == []


def test_get_posts_returns_list_of_post_shapes(client, alice):
    client.post("/posts", json={"message": "a"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "b"}, headers={"X-Username": "alice"})
    body = client.get("/posts").json()
    assert len(body) == 2
    for item in body:
        assert set(item.keys()) == POST_FIELDS


def test_get_post_by_id_200(client, alice):
    post = client.post("/posts",
                       json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    r = client.get(f"/posts/{post['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == post["id"]


def test_get_post_by_id_has_exact_fields(client, alice):
    post = client.post("/posts",
                       json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    body = client.get(f"/posts/{post['id']}").json()
    assert set(body.keys()) == POST_FIELDS


def test_get_post_by_nonexistent_id_404(client):
    r = client.get("/posts/99999999")
    assert r.status_code == 404


def test_get_post_by_string_id_422(client):
    # FastAPI path-param type coercion: {id:int} rejects "abc" with 422.
    # Locking this in means a refactor to str() path won't silently
    # degrade the API.
    r = client.get("/posts/not-a-number")
    assert r.status_code == 422


def test_get_user_posts_returns_only_that_users(client, alice, bob):
    client.post("/posts", json={"message": "a1"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "a2"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "b1"}, headers={"X-Username": "bob"})
    body = client.get("/users/alice/posts").json()
    assert len(body) == 2
    assert all(p["username"] == "alice" for p in body)


def test_get_user_posts_items_have_exact_fields(client, alice):
    client.post("/posts", json={"message": "a"}, headers={"X-Username": "alice"})
    body = client.get("/users/alice/posts").json()
    assert set(body[0].keys()) == POST_FIELDS


def test_get_user_posts_empty_for_user_who_never_posted(client, alice):
    # A user who exists but has never posted should get 200 + [],
    # not 404.  The user EXISTS; their post list is simply empty.
    r = client.get("/users/alice/posts")
    assert r.status_code == 200
    assert r.json() == []


def test_get_user_posts_404_when_user_missing(client):
    r = client.get("/users/ghost/posts")
    assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════════
#  Search — GET /posts?q=
# ══════════════════════════════════════════════════════════════════════

def _seed_search_corpus(client):
    """Create a small corpus that most search tests can share."""
    client.post("/users", json={"username": "alice"})
    client.post("/posts", json={"message": "Hello world"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "goodbye world"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "HELLO AGAIN"},
                headers={"X-Username": "alice"})


def test_search_finds_matching_posts(client):
    _seed_search_corpus(client)
    body = client.get("/posts", params={"q": "world"}).json()
    messages = [p["message"] for p in body]
    assert "Hello world" in messages
    assert "goodbye world" in messages
    assert "HELLO AGAIN" not in messages


def test_search_is_case_insensitive(client):
    # A1's SQLite LIKE is case-insensitive for ASCII.  We match that
    # behaviour so searches feel natural ("hello" finds "Hello").
    _seed_search_corpus(client)
    body = client.get("/posts", params={"q": "hello"}).json()
    msgs = [p["message"] for p in body]
    assert "Hello world" in msgs
    assert "HELLO AGAIN" in msgs


def test_search_no_matches_returns_empty_array(client):
    _seed_search_corpus(client)
    r = client.get("/posts", params={"q": "zzzzzzzzz"})
    assert r.status_code == 200
    assert r.json() == []


def test_search_literal_percent_does_not_act_as_wildcard(client, alice):
    # CRITICAL EDGE CASE.  The obvious implementation is:
    #     WHERE message LIKE '%' || ? || '%'
    # with the user's q bound in the middle.  SQL LIKE's `%` and `_`
    # are wildcards — if we forget to escape them, q="%" matches
    # every post (effectively unfiltered).  This test locks in
    # literal treatment.
    client.post("/posts", json={"message": "plain message"},
                headers={"X-Username": "alice"})
    r = client.get("/posts", params={"q": "%"})
    assert r.status_code == 200
    assert r.json() == [], "`%` must be treated as a literal, not a wildcard"


def test_search_literal_underscore_does_not_act_as_wildcard(client, alice):
    # Same reasoning as `%` above — SQL LIKE's `_` matches any single
    # character.  q="_" must NOT match any post just because posts
    # exist.
    client.post("/posts", json={"message": "plain"},
                headers={"X-Username": "alice"})
    r = client.get("/posts", params={"q": "_"})
    assert r.status_code == 200
    assert r.json() == []


# ══════════════════════════════════════════════════════════════════════
#  Pagination — limit & offset on GET /posts
# ══════════════════════════════════════════════════════════════════════

def _seed_many_posts(client, n):
    """Create one user and N sequentially-messaged posts."""
    client.post("/users", json={"username": "alice"})
    for i in range(n):
        client.post("/posts", json={"message": f"msg {i}"},
                    headers={"X-Username": "alice"})


def test_limit_caps_results(client):
    _seed_many_posts(client, 5)
    body = client.get("/posts", params={"limit": 2}).json()
    assert len(body) == 2


def test_limit_larger_than_data_returns_all(client):
    _seed_many_posts(client, 3)
    body = client.get("/posts", params={"limit": 100}).json()
    assert len(body) == 3


def test_limit_at_max_passes(client):
    # 200 is the documented upper bound — must not 422.
    r = client.get("/posts", params={"limit": 200})
    assert r.status_code == 200


def test_limit_at_min_passes(client):
    r = client.get("/posts", params={"limit": 1})
    assert r.status_code == 200


def test_limit_zero_returns_422(client):
    r = client.get("/posts", params={"limit": 0})
    assert r.status_code == 422


def test_limit_over_max_returns_422(client):
    r = client.get("/posts", params={"limit": 201})
    assert r.status_code == 422


def test_limit_very_large_returns_422(client):
    r = client.get("/posts", params={"limit": 500})
    assert r.status_code == 422


def test_limit_negative_returns_422(client):
    r = client.get("/posts", params={"limit": -1})
    assert r.status_code == 422


def test_limit_non_integer_returns_422(client):
    # FastAPI coerces query strings to int.  "abc" won't coerce.
    r = client.get("/posts", params={"limit": "abc"})
    assert r.status_code == 422


def test_offset_skips_results(client):
    # Create 5 posts, skip the first 2, ask for the rest.  The three
    # returned ids should be exactly the tail of the full list
    # (order-agnostic check via set equality).
    _seed_many_posts(client, 5)
    full = client.get("/posts", params={"limit": 10}).json()
    assert len(full) == 5

    tail = client.get("/posts", params={"offset": 2, "limit": 10}).json()
    assert len(tail) == 3

    # Whatever order the server uses, skipping N means leaving behind
    # exactly N ids.  Set-diff makes this order-independent.
    full_ids = {p["id"] for p in full}
    tail_ids = {p["id"] for p in tail}
    assert len(full_ids - tail_ids) == 2


def test_offset_at_zero_returns_everything(client):
    _seed_many_posts(client, 3)
    body = client.get("/posts", params={"offset": 0}).json()
    assert len(body) == 3


def test_offset_beyond_data_returns_empty(client):
    _seed_many_posts(client, 3)
    body = client.get("/posts", params={"offset": 100}).json()
    assert body == []


def test_offset_negative_returns_422(client):
    r = client.get("/posts", params={"offset": -1})
    assert r.status_code == 422


def test_limit_and_offset_compose(client):
    # limit=2 + offset=1 on 5 posts → the middle-ish 2.
    _seed_many_posts(client, 5)
    body = client.get("/posts", params={"limit": 2, "offset": 1}).json()
    assert len(body) == 2


def test_limit_and_q_compose(client, alice):
    # Search + pagination should compose.  Filter first, paginate the
    # filtered result — not paginate then filter (which would give
    # inconsistent counts).
    for i in range(5):
        client.post("/posts", json={"message": f"keep {i}"},
                    headers={"X-Username": "alice"})
    for i in range(5):
        client.post("/posts", json={"message": f"drop {i}"},
                    headers={"X-Username": "alice"})
    body = client.get("/posts", params={"q": "keep", "limit": 3}).json()
    assert len(body) == 3
    assert all("keep" in p["message"] for p in body)


# ══════════════════════════════════════════════════════════════════════
#  DELETE /posts/{id}
# ══════════════════════════════════════════════════════════════════════

def test_delete_existing_post_returns_204(client, alice):
    pid = client.post("/posts",
                      json={"message": "bye"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.delete(f"/posts/{pid}")
    assert r.status_code == 204


def test_delete_returns_empty_body(client, alice):
    # 204 responses are not supposed to carry a body.  If your handler
    # returns `{"ok": true}` with status_code=204, httpx will usually
    # surface the body anyway and we want to catch that.
    pid = client.post("/posts",
                      json={"message": "bye"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.delete(f"/posts/{pid}")
    assert r.content == b""


def test_get_after_delete_returns_404(client, alice):
    pid = client.post("/posts",
                      json={"message": "bye"},
                      headers={"X-Username": "alice"}).json()["id"]
    client.delete(f"/posts/{pid}")
    r = client.get(f"/posts/{pid}")
    assert r.status_code == 404


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/posts/99999999")
    assert r.status_code == 404


def test_delete_twice_second_is_404(client, alice):
    # After a successful DELETE, the post is gone — deleting again
    # behaves exactly like deleting any other missing id.
    pid = client.post("/posts",
                      json={"message": "bye"},
                      headers={"X-Username": "alice"}).json()["id"]
    assert client.delete(f"/posts/{pid}").status_code == 204
    assert client.delete(f"/posts/{pid}").status_code == 404


def test_delete_removes_from_list(client, alice):
    pids = [client.post("/posts",
                        json={"message": f"m{i}"},
                        headers={"X-Username": "alice"}).json()["id"]
            for i in range(3)]
    client.delete(f"/posts/{pids[1]}")
    remaining_ids = {p["id"] for p in client.get("/posts").json()}
    assert pids[1] not in remaining_ids
    assert pids[0] in remaining_ids
    assert pids[2] in remaining_ids


def test_delete_removes_from_user_posts(client, alice):
    pid = client.post("/posts",
                      json={"message": "bye"},
                      headers={"X-Username": "alice"}).json()["id"]
    client.delete(f"/posts/{pid}")
    body = client.get("/users/alice/posts").json()
    assert all(p["id"] != pid for p in body)


# ══════════════════════════════════════════════════════════════════════
#  Cross-cutting: error body shape
# ══════════════════════════════════════════════════════════════════════

def test_404_body_shape(client):
    # The spec standardises on FastAPI's default {"detail": "..."}.
    # Two representative 404s — one handcrafted (user lookup), one
    # pydantic-driven (int path param) — both should have `detail`.
    assert "detail" in client.get("/users/ghost").json()
    assert "detail" in client.get("/posts/99999999").json()


def test_409_body_shape(client, alice):
    # HTTPException(409, "...") yields {"detail": "..."} too.
    r = client.post("/users", json={"username": "alice"})
    assert r.status_code == 409
    assert "detail" in r.json()


# ══════════════════════════════════════════════════════════════════════
#  SILVER — user shape additions (bio + post_count)
# ══════════════════════════════════════════════════════════════════════
#
# Silver widens the user object from {username, created_at} to
# {username, created_at, bio, post_count}.  The field-shape constants
# at the top of this file already reflect the new sets, so every
# existing "exact fields" test automatically checks them.  The tests
# below pin down the SEMANTICS of the two new fields — specifically,
# what values they take under what conditions.

def test_silver_fresh_user_has_null_bio(client):
    # A brand-new user has never set a bio, so it is JSON null.
    # This is a conscious default-null choice; the spec allows either
    # null or absent-with-a-getter, and JSON null is the cleanest.
    body = client.post("/users", json={"username": "alice"}).json()
    assert body["bio"] is None


def test_silver_fresh_user_has_post_count_zero(client):
    # Computed field — never stored, derived from the posts table on
    # read.  A user who just came into existence has posted nothing.
    body = client.post("/users", json={"username": "alice"}).json()
    assert body["post_count"] == 0


def test_silver_post_count_increments_with_each_post(client, alice):
    # post_count is computed, not stored, so each lookup must reflect
    # the current state of the posts table.
    assert client.get("/users/alice").json()["post_count"] == 0
    client.post("/posts", json={"message": "m1"}, headers={"X-Username": "alice"})
    assert client.get("/users/alice").json()["post_count"] == 1
    client.post("/posts", json={"message": "m2"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "m3"}, headers={"X-Username": "alice"})
    assert client.get("/users/alice").json()["post_count"] == 3


def test_silver_post_count_decrements_after_delete(client, alice):
    # The other direction: deleting a post must reduce post_count.
    # Specifically tests that the field is computed per-request, not
    # cached somewhere that would go stale after a DELETE.
    ids = [
        client.post("/posts", json={"message": f"m{i}"},
                    headers={"X-Username": "alice"}).json()["id"]
        for i in range(3)
    ]
    assert client.get("/users/alice").json()["post_count"] == 3
    client.delete(f"/posts/{ids[0]}")
    assert client.get("/users/alice").json()["post_count"] == 2
    client.delete(f"/posts/{ids[1]}")
    client.delete(f"/posts/{ids[2]}")
    assert client.get("/users/alice").json()["post_count"] == 0


def test_silver_list_users_has_post_count_per_user(client, alice, bob):
    # When GET /users joins posts for counts, a naive JOIN can drop
    # rows for users with no posts.  This test catches a LEFT JOIN
    # that's actually an INNER JOIN in disguise.
    client.post("/posts", json={"message": "x"}, headers={"X-Username": "alice"})
    body = client.get("/users").json()
    counts = {u["username"]: u["post_count"] for u in body}
    assert counts == {"alice": 1, "bob": 0}


def test_silver_list_users_items_have_all_four_fields(client, alice):
    # The expanded USER_FIELDS constant locks this down, but making
    # it a dedicated silver test documents the intent.
    item = client.get("/users").json()[0]
    assert set(item.keys()) == {"username", "created_at", "bio", "post_count"}


def test_silver_bio_is_preserved_in_get(client, alice):
    # After PATCH sets bio, subsequent GETs should return the same
    # value.  This exists so "bio in list != bio by id" regressions
    # surface loudly.
    client.patch("/users/alice", json={"bio": "wolf in dog suit"})
    single = client.get("/users/alice").json()
    listed = next(u for u in client.get("/users").json() if u["username"] == "alice")
    assert single["bio"] == "wolf in dog suit"
    assert listed["bio"] == "wolf in dog suit"


# ══════════════════════════════════════════════════════════════════════
#  SILVER — post shape additions (updated_at)
# ══════════════════════════════════════════════════════════════════════

def test_silver_fresh_post_updated_at_is_null(client, alice):
    # Never-edited posts carry updated_at=null, not a copy of
    # created_at.  null cleanly signals "never touched".
    body = client.post("/posts", json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    assert body["updated_at"] is None


def test_silver_fresh_post_has_five_fields(client, alice):
    # Originally named "five fields" in silver-era; the test now
    # checks the current tier's shape (six fields as of gold).  Uses
    # POST_FIELDS so this assertion stays correct across tier bumps.
    body = client.post("/posts", json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    assert set(body.keys()) == POST_FIELDS


def test_silver_get_post_by_id_includes_updated_at(client, alice):
    pid = client.post("/posts", json={"message": "hi"},
                      headers={"X-Username": "alice"}).json()["id"]
    body = client.get(f"/posts/{pid}").json()
    assert "updated_at" in body


def test_silver_list_posts_items_include_updated_at(client, alice):
    client.post("/posts", json={"message": "a"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "b"}, headers={"X-Username": "alice"})
    for item in client.get("/posts").json():
        assert "updated_at" in item


# ══════════════════════════════════════════════════════════════════════
#  SILVER — PATCH /users/{username}
# ══════════════════════════════════════════════════════════════════════
#
# Semantics:
#   - Body {"bio": "x"} sets bio to "x".
#   - Body {"bio": null} clears bio back to null.
#   - Body {} is a 200 no-op (nothing to update, nothing fails).
#   - Any other fields in the body are silently ignored (PATCH is
#     tolerant by design — clients shipping future versions of the
#     shape should not crash current servers).
#   - The response body is the full updated user in the silver shape.

def test_patch_user_bio_returns_200(client, alice):
    r = client.patch("/users/alice", json={"bio": "hello"})
    assert r.status_code == 200


def test_patch_user_bio_updates_value(client, alice):
    client.patch("/users/alice", json={"bio": "hello"})
    assert client.get("/users/alice").json()["bio"] == "hello"


def test_patch_user_bio_at_max_length_passes(client, alice):
    # Boundary: exactly 200 chars is the documented cap.
    r = client.patch("/users/alice", json={"bio": "x" * 200})
    assert r.status_code == 200


def test_patch_user_bio_too_long_returns_422(client, alice):
    # 201 chars — one over.
    r = client.patch("/users/alice", json={"bio": "x" * 201})
    assert r.status_code == 422


def test_patch_user_bio_null_clears_existing(client, alice):
    # Two PATCHes: first sets a bio, second clears it back to null.
    # Distinguishes "clear" (explicit null) from "leave alone" (omitted).
    client.patch("/users/alice", json={"bio": "something"})
    assert client.get("/users/alice").json()["bio"] == "something"
    client.patch("/users/alice", json={"bio": None})
    assert client.get("/users/alice").json()["bio"] is None


def test_patch_user_empty_body_is_noop_200(client, alice):
    # PATCH with {} means "no changes requested".  200 with current
    # state is the idiomatic response — not 400, not 422.
    client.patch("/users/alice", json={"bio": "kept"})
    r = client.patch("/users/alice", json={})
    assert r.status_code == 200
    assert r.json()["bio"] == "kept"   # unchanged


def test_patch_user_404_when_missing(client):
    r = client.patch("/users/ghost", json={"bio": "hi"})
    assert r.status_code == 404


def test_patch_user_response_has_silver_shape(client, alice):
    # The response is a full UserOut, not just the patched field.
    body = client.patch("/users/alice", json={"bio": "hi"}).json()
    assert set(body.keys()) == {"username", "created_at", "bio", "post_count"}


def test_patch_user_ignores_unknown_fields(client, alice):
    # Forward-compat: a client shipping a future "avatar" field
    # should not break on today's server.  Unknown keys silently drop.
    r = client.patch("/users/alice",
                     json={"bio": "kept", "avatar": "x.png", "username": "mallory"})
    assert r.status_code == 200
    assert r.json()["username"] == "alice"   # NOT "mallory"
    assert r.json()["bio"] == "kept"
    assert "avatar" not in r.json()


def test_patch_user_bio_empty_string_allowed(client, alice):
    # Distinct from null.  "" is a valid (empty) bio — a user who
    # actively cleared their bio text without nulling the field.
    # If the implementation accidentally treats "" as null the
    # response would show None here.
    r = client.patch("/users/alice", json={"bio": ""})
    assert r.status_code == 200
    assert r.json()["bio"] == ""


def test_patch_user_bio_preserves_unicode(client, alice):
    bio = "café ☕ 世界"
    client.patch("/users/alice", json={"bio": bio})
    assert client.get("/users/alice").json()["bio"] == bio


def test_patch_user_bio_preserves_newlines(client, alice):
    bio = "line one\nline two"
    client.patch("/users/alice", json={"bio": bio})
    assert client.get("/users/alice").json()["bio"] == bio


def test_patch_user_non_string_bio_returns_422(client, alice):
    # Type check: bio must be a string (or null).  A number is a
    # type error — Pydantic rejects it.
    r = client.patch("/users/alice", json={"bio": 42})
    assert r.status_code == 422


def test_patch_user_preserves_post_count(client, alice):
    # Bio updates are orthogonal to the posts count.  This test
    # guards against a future refactor that accidentally invalidates
    # post_count on user write paths.
    client.post("/posts", json={"message": "x"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "y"}, headers={"X-Username": "alice"})
    body = client.patch("/users/alice", json={"bio": "hi"}).json()
    assert body["post_count"] == 2


# ══════════════════════════════════════════════════════════════════════
#  SILVER — PATCH /posts/{id}  (ownership = X-Username match)
# ══════════════════════════════════════════════════════════════════════
#
# Ownership policy chosen: only the original author (the username
# stored on the post) can edit it, and that identity is claimed via
# the X-Username header.  Yes, X-Username is not real authentication
# — but wiring the CONCEPT of ownership in now means the switch to
# real auth later is a one-line change in the identity check.
#
# Status-code plan for PATCH /posts/{id}:
#   400 — X-Username missing or empty
#   404 — post does not exist
#   403 — post exists but X-Username does not match the author
#   422 — message body fails validation (empty, too long, null)
#   200 — OK
#
# Order of checks: header presence (400) → post lookup (404) →
# ownership (403) → body validation (handled by Pydantic before the
# handler runs, so 422 wins on malformed bodies regardless).

def test_patch_post_returns_200(client, alice):
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": "new"},
                     headers={"X-Username": "alice"})
    assert r.status_code == 200


def test_patch_post_updates_message(client, alice):
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    client.patch(f"/posts/{pid}", json={"message": "new"},
                 headers={"X-Username": "alice"})
    assert client.get(f"/posts/{pid}").json()["message"] == "new"


def test_patch_post_sets_updated_at(client, alice):
    # Before PATCH: updated_at is null.
    # After PATCH: updated_at is a non-null ISO string.
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    assert client.get(f"/posts/{pid}").json()["updated_at"] is None
    client.patch(f"/posts/{pid}", json={"message": "new"},
                 headers={"X-Username": "alice"})
    updated_at = client.get(f"/posts/{pid}").json()["updated_at"]
    assert updated_at is not None
    assert ISO_SECONDS_RE.match(updated_at)


def test_patch_post_max_length_passes(client, alice):
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": "x" * 500},
                     headers={"X-Username": "alice"})
    assert r.status_code == 200


def test_patch_post_empty_message_returns_422(client, alice):
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": ""},
                     headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_patch_post_too_long_returns_422(client, alice):
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": "x" * 501},
                     headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_patch_post_null_message_returns_422(client, alice):
    # An explicit null message is nonsensical (a post needs a
    # message), and different from omitting the field.  Must fail.
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": None},
                     headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_patch_post_missing_x_username_returns_400(client, alice):
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": "new"})
    assert r.status_code == 400


def test_patch_post_empty_x_username_returns_400(client, alice):
    # Same as POST /posts: empty string treated as missing.
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": "new"},
                     headers={"X-Username": ""})
    assert r.status_code == 400


def test_patch_post_wrong_author_returns_403(client, alice, bob):
    # Ownership check.  alice's post, bob tries to edit.  403 is the
    # correct code — the request is well-formed, but the caller is
    # not authorized for this specific resource.  A 401 would mean
    # "who are you?" (we don't know) but here we know who the caller
    # claims to be; they just don't own the post.
    pid = client.post("/posts", json={"message": "alice's"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={"message": "bob's"},
                     headers={"X-Username": "bob"})
    assert r.status_code == 403


def test_patch_post_wrong_author_does_not_update(client, alice, bob):
    # Defense in depth: the 403 must be a true refusal, not "I
    # returned 403 but the write already happened".
    pid = client.post("/posts", json={"message": "alice's"},
                      headers={"X-Username": "alice"}).json()["id"]
    client.patch(f"/posts/{pid}", json={"message": "bob's"},
                 headers={"X-Username": "bob"})
    assert client.get(f"/posts/{pid}").json()["message"] == "alice's"
    assert client.get(f"/posts/{pid}").json()["updated_at"] is None


def test_patch_post_nonexistent_returns_404(client, alice):
    r = client.patch("/posts/99999999", json={"message": "new"},
                     headers={"X-Username": "alice"})
    assert r.status_code == 404


def test_patch_post_nonexistent_trumps_wrong_author(client, alice):
    # Order-of-checks test: if the post doesn't exist, we return 404
    # even when the X-Username is unknown.  Existence is checked
    # before ownership.  (The alternative — 403 everywhere to avoid
    # leaking whether the id exists — is an infosec hardening move,
    # but 404 is the plain-REST default.)
    r = client.patch("/posts/99999999", json={"message": "new"},
                     headers={"X-Username": "ghost"})
    assert r.status_code == 404


def test_patch_post_empty_body_is_noop_200(client, alice):
    pid = client.post("/posts", json={"message": "kept"},
                      headers={"X-Username": "alice"}).json()["id"]
    r = client.patch(f"/posts/{pid}", json={},
                     headers={"X-Username": "alice"})
    assert r.status_code == 200
    assert r.json()["message"] == "kept"


def test_patch_post_empty_body_does_not_set_updated_at(client, alice):
    # If the PATCH changed nothing, updated_at stays null.  Bumping
    # it on a no-op would lie about resource history.
    pid = client.post("/posts", json={"message": "kept"},
                      headers={"X-Username": "alice"}).json()["id"]
    client.patch(f"/posts/{pid}", json={},
                 headers={"X-Username": "alice"})
    assert client.get(f"/posts/{pid}").json()["updated_at"] is None


def test_patch_post_response_has_silver_shape(client, alice):
    # "silver_shape" is a historical name — we use the current tier
    # (gold) constant so this survives further tier bumps.
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    body = client.patch(f"/posts/{pid}", json={"message": "new"},
                        headers={"X-Username": "alice"}).json()
    assert set(body.keys()) == POST_FIELDS


def test_patch_post_preserves_unicode(client, alice):
    pid = client.post("/posts", json={"message": "old"},
                      headers={"X-Username": "alice"}).json()["id"]
    new = "héllo 🌮 世界"
    client.patch(f"/posts/{pid}", json={"message": new},
                 headers={"X-Username": "alice"})
    assert client.get(f"/posts/{pid}").json()["message"] == new


def test_patch_post_twice_refreshes_updated_at(client, alice):
    # A second PATCH should (re)set updated_at to a valid ISO string.
    # We don't assert the second > the first because seconds-level
    # precision can tie on a fast machine; we just require the field
    # is still set and well-formed.
    pid = client.post("/posts", json={"message": "v0"},
                      headers={"X-Username": "alice"}).json()["id"]
    client.patch(f"/posts/{pid}", json={"message": "v1"},
                 headers={"X-Username": "alice"})
    ts1 = client.get(f"/posts/{pid}").json()["updated_at"]
    client.patch(f"/posts/{pid}", json={"message": "v2"},
                 headers={"X-Username": "alice"})
    ts2 = client.get(f"/posts/{pid}").json()["updated_at"]
    assert ts1 is not None and ISO_SECONDS_RE.match(ts1)
    assert ts2 is not None and ISO_SECONDS_RE.match(ts2)


# ══════════════════════════════════════════════════════════════════════
#  SILVER — GET /posts?username=  (filter by author)
# ══════════════════════════════════════════════════════════════════════
#
# ?username= is a filter on the posts list.  It composes with ?q=
# (intersection) and with limit/offset (filter first, paginate the
# filtered result).  Unknown usernames return 200 [] — this is a
# FILTER, not a LOOKUP, so "no matches" is the right semantic, not
# "resource missing".

def test_filter_by_username_returns_only_that_user(client, alice, bob):
    client.post("/posts", json={"message": "a1"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "a2"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "b1"}, headers={"X-Username": "bob"})
    body = client.get("/posts", params={"username": "alice"}).json()
    assert len(body) == 2
    assert all(p["username"] == "alice" for p in body)


def test_filter_by_unknown_username_returns_empty_array(client, alice):
    # Contrast with GET /users/{username}/posts, where an unknown
    # user is 404.  There, {username} is a PATH parameter — part of
    # the resource identifier — so missing = 404.  Here, ?username=
    # is a QUERY parameter — a filter — so no match = [].
    client.post("/posts", json={"message": "hi"}, headers={"X-Username": "alice"})
    r = client.get("/posts", params={"username": "ghost"})
    assert r.status_code == 200
    assert r.json() == []


def test_filter_by_username_empty_for_user_with_no_posts(client, alice):
    # Valid user, zero posts — also 200 [].
    r = client.get("/posts", params={"username": "alice"})
    assert r.status_code == 200
    assert r.json() == []


def test_filter_by_username_composes_with_q(client, alice, bob):
    # Intersection: alice's posts AND containing "keep".
    # Hits both the filter and the search at the same time.
    client.post("/posts", json={"message": "alice keep"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "alice drop"}, headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "bob keep"},   headers={"X-Username": "bob"})
    body = client.get("/posts", params={"username": "alice", "q": "keep"}).json()
    assert len(body) == 1
    assert body[0]["message"] == "alice keep"


def test_filter_by_username_composes_with_limit(client, alice, bob):
    # 5 alice posts, filter to alice, limit 3 → 3 alice posts.
    # Confirms that limit applies to the FILTERED result, not the
    # raw posts table.
    for i in range(5):
        client.post("/posts", json={"message": f"a{i}"},
                    headers={"X-Username": "alice"})
    for i in range(5):
        client.post("/posts", json={"message": f"b{i}"},
                    headers={"X-Username": "bob"})
    body = client.get("/posts", params={"username": "alice", "limit": 3}).json()
    assert len(body) == 3
    assert all(p["username"] == "alice" for p in body)


def test_filter_by_username_composes_with_offset(client, alice):
    # 5 posts, filter to alice (all of them), offset 2 → 3 posts.
    for i in range(5):
        client.post("/posts", json={"message": f"a{i}"},
                    headers={"X-Username": "alice"})
    body = client.get("/posts", params={"username": "alice", "offset": 2}).json()
    assert len(body) == 3


def test_filter_by_username_plus_q_plus_pagination(client, alice, bob):
    # Full composition: username=alice, q=keep, limit=2.
    # 3 alice-keep posts; limit 2 should return 2 of them.
    for i in range(3):
        client.post("/posts", json={"message": f"keep {i}"},
                    headers={"X-Username": "alice"})
    for i in range(3):
        client.post("/posts", json={"message": f"drop {i}"},
                    headers={"X-Username": "alice"})
    for i in range(3):
        client.post("/posts", json={"message": f"keep {i}"},
                    headers={"X-Username": "bob"})
    body = client.get("/posts",
                      params={"username": "alice", "q": "keep", "limit": 2}).json()
    assert len(body) == 2
    assert all(p["username"] == "alice" for p in body)
    assert all("keep" in p["message"] for p in body)


# ══════════════════════════════════════════════════════════════════════
#  GOLD — post shape additions (board field)
# ══════════════════════════════════════════════════════════════════════
#
# Gold widens the post object from five fields to six.  The new field
# is `board`: a non-null string, default "general", must match
# ^[a-zA-Z0-9_]+$ and be at most 32 chars.  Everything in this
# section pins down the DEFAULTING and ROUND-TRIP behavior of that
# new field — the validation rules are in the next section.

def test_gold_fresh_post_defaults_to_general_board(client, alice):
    # POST /posts with no "board" key in the body must land on the
    # "general" board.  This is the contract that keeps every bronze
    # and silver client working unchanged after the schema bump.
    body = client.post("/posts", json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    assert body["board"] == "general"


def test_gold_post_accepts_explicit_board(client, alice):
    # Body {"message": "...", "board": "tech"} → post lives on "tech".
    # Covers the common case of a client targeting a specific board.
    body = client.post("/posts", json={"message": "hi", "board": "tech"},
                       headers={"X-Username": "alice"}).json()
    assert body["board"] == "tech"


def test_gold_post_response_has_six_fields(client, alice):
    # The shape constant at the top of the file pins this, but a
    # dedicated test documents the intent and fails loudly if somebody
    # edits the constant without reading this file.
    body = client.post("/posts", json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    assert set(body.keys()) == {
        "id", "username", "message", "created_at", "updated_at", "board",
    }


def test_gold_get_post_by_id_includes_board(client, alice):
    # Lookup path must include `board` too — not just the create path.
    # Guards against a common "I added the field to POST but forgot
    # the SELECT" regression.
    pid = client.post("/posts", json={"message": "hi", "board": "tech"},
                      headers={"X-Username": "alice"}).json()["id"]
    body = client.get(f"/posts/{pid}").json()
    assert body["board"] == "tech"


def test_gold_list_posts_items_include_board(client, alice):
    # And the list path.  Same regression class.
    client.post("/posts", json={"message": "a", "board": "tech"},
                headers={"X-Username": "alice"})
    for item in client.get("/posts").json():
        assert "board" in item


def test_gold_user_posts_items_include_board(client, alice):
    client.post("/posts", json={"message": "a", "board": "tech"},
                headers={"X-Username": "alice"})
    for item in client.get("/users/alice/posts").json():
        assert "board" in item


def test_gold_patch_preserves_board(client, alice):
    # PATCH edits the message — it does NOT touch the board column.
    # A sloppy UPDATE that SETs every column would blank this out.
    pid = client.post("/posts", json={"message": "old", "board": "tech"},
                      headers={"X-Username": "alice"}).json()["id"]
    client.patch(f"/posts/{pid}", json={"message": "new"},
                 headers={"X-Username": "alice"})
    assert client.get(f"/posts/{pid}").json()["board"] == "tech"


# ══════════════════════════════════════════════════════════════════════
#  GOLD — board-name validation on POST /posts
# ══════════════════════════════════════════════════════════════════════

def test_gold_post_board_at_max_length_passes(client, alice):
    # Boundary: exactly 32 chars passes.
    r = client.post("/posts", json={"message": "x", "board": "a" * 32},
                    headers={"X-Username": "alice"})
    assert r.status_code == 201


def test_gold_post_board_too_long_returns_422(client, alice):
    # One over the cap.
    r = client.post("/posts", json={"message": "x", "board": "a" * 33},
                    headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_gold_post_board_empty_string_returns_422(client, alice):
    # "" doesn't match the regex (needs at least one char), so 422.
    r = client.post("/posts", json={"message": "x", "board": ""},
                    headers={"X-Username": "alice"})
    assert r.status_code == 422


@pytest.mark.parametrize("bad", ["has spaces", "with-dash", "with.dot", "wíth unicode", "#hash"])
def test_gold_post_board_invalid_chars_return_422(client, alice, bad):
    # The regex is ^[a-zA-Z0-9_]+$, so all of these fail.  Parametrize
    # so a single test name covers several shapes of input.
    r = client.post("/posts", json={"message": "x", "board": bad},
                    headers={"X-Username": "alice"})
    assert r.status_code == 422, f"{bad!r} was accepted"


def test_gold_post_board_null_returns_422(client, alice):
    # Unlike silver's `bio`, `board` is a non-optional string.
    # Explicit null is a type error — 422 from Pydantic.
    r = client.post("/posts", json={"message": "x", "board": None},
                    headers={"X-Username": "alice"})
    assert r.status_code == 422


def test_gold_post_board_underscore_allowed(client, alice):
    # Underscores are in the regex.  A dedicated pass-case test so
    # a tightening of the regex doesn't silently break existing users.
    r = client.post("/posts", json={"message": "x", "board": "my_board"},
                    headers={"X-Username": "alice"})
    assert r.status_code == 201


# ══════════════════════════════════════════════════════════════════════
#  GOLD — GET /boards
# ══════════════════════════════════════════════════════════════════════
#
# Implicit-boards model: a board exists exactly when at least one
# post references it.  GET /boards returns the set of boards that
# currently have posts, with counts.  No separate registry; no way
# to reserve an empty board.  If an empty-board registry is ever
# needed, that's a separate feature.

def test_gold_get_boards_empty_returns_empty_array(client):
    # Fresh DB, no posts → no boards.  [] (not null, not 404).
    r = client.get("/boards")
    assert r.status_code == 200
    assert r.json() == []


def test_gold_get_boards_is_bare_array(client, alice):
    # Same "no envelope objects" discipline as the bronze list
    # endpoints — consistency across the API matters.
    client.post("/posts", json={"message": "x"}, headers={"X-Username": "alice"})
    body = client.get("/boards").json()
    assert isinstance(body, list)


def test_gold_get_boards_items_have_shape_name_post_count(client, alice):
    # Each item is {name, post_count}.  Kept small on purpose —
    # adding created_at, description, etc. later would be trivial.
    client.post("/posts", json={"message": "x"}, headers={"X-Username": "alice"})
    item = client.get("/boards").json()[0]
    assert set(item.keys()) == {"name", "post_count"}


def test_gold_get_boards_includes_post_counts(client, alice):
    # Two boards with different counts — the GROUP BY must be right.
    for _ in range(3):
        client.post("/posts", json={"message": "x", "board": "tech"},
                    headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "y", "board": "music"},
                headers={"X-Username": "alice"})
    body = client.get("/boards").json()
    counts = {b["name"]: b["post_count"] for b in body}
    assert counts == {"tech": 3, "music": 1}


def test_gold_get_boards_only_lists_boards_with_posts(client, alice):
    # Implicit-boards semantics: a board only shows up when posted to.
    # POSTing to "tech" should not make "music" appear.
    client.post("/posts", json={"message": "x", "board": "tech"},
                headers={"X-Username": "alice"})
    names = [b["name"] for b in client.get("/boards").json()]
    assert names == ["tech"]


def test_gold_get_boards_reflects_deletes(client, alice):
    # Deleting the last post in a board makes that board disappear
    # from GET /boards.  No row → no group → no item.  Mirrors how
    # post_count works on users.
    pid = client.post("/posts", json={"message": "only", "board": "tech"},
                      headers={"X-Username": "alice"}).json()["id"]
    assert any(b["name"] == "tech" for b in client.get("/boards").json())
    client.delete(f"/posts/{pid}")
    assert not any(b["name"] == "tech" for b in client.get("/boards").json())


# ══════════════════════════════════════════════════════════════════════
#  GOLD — GET /boards/{name}/posts
# ══════════════════════════════════════════════════════════════════════

def test_gold_get_board_posts_returns_only_that_boards_posts(client, alice):
    # Filter discipline: posts on "tech" + posts on "music" →
    # /boards/tech/posts returns only the tech ones.  Off-by-board
    # bugs (forgetting the WHERE) show up here.
    client.post("/posts", json={"message": "t1", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "t2", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "m1", "board": "music"},
                headers={"X-Username": "alice"})
    body = client.get("/boards/tech/posts").json()
    assert len(body) == 2
    assert all(p["board"] == "tech" for p in body)


def test_gold_get_board_posts_empty_for_unposted_board(client):
    # Under implicit-boards, an un-posted board simply has no rows.
    # Convention choice: 200 [] rather than 404.  Reasoning: the
    # endpoint is a FILTER over posts, not a LOOKUP of a board
    # resource (which does not exist as a table row to "miss").
    r = client.get("/boards/nonexistent/posts")
    assert r.status_code == 200
    assert r.json() == []


def test_gold_get_board_posts_items_have_full_post_shape(client, alice):
    # Must return the same 6-field gold post object as everything else
    # in /posts-land.  Regressions here usually come from a custom
    # response_model on this endpoint forgetting a field.
    client.post("/posts", json={"message": "t1", "board": "tech"},
                headers={"X-Username": "alice"})
    item = client.get("/boards/tech/posts").json()[0]
    assert set(item.keys()) == {
        "id", "username", "message", "created_at", "updated_at", "board",
    }


def test_gold_get_board_posts_does_not_include_other_boards(client, alice):
    # A second form of the "only that board" test but phrased as
    # "messages from the other board must NOT appear" — useful for
    # catching a WHERE clause that uses OR instead of AND.
    client.post("/posts", json={"message": "tech-only", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "music-only", "board": "music"},
                headers={"X-Username": "alice"})
    messages = [p["message"] for p in client.get("/boards/tech/posts").json()]
    assert "tech-only" in messages
    assert "music-only" not in messages


# ══════════════════════════════════════════════════════════════════════
#  GOLD — POST /boards/{name}/posts  (convenience creation endpoint)
# ══════════════════════════════════════════════════════════════════════
#
# POST /boards/tech/posts with body {"message": "hi"} is equivalent
# to POST /posts with body {"message": "hi", "board": "tech"}.
# The URL wins — the body does not accept a "board" field here
# (we simply don't define it on the Pydantic model for this endpoint).

def test_gold_post_to_board_returns_201(client, alice):
    r = client.post("/boards/tech/posts", json={"message": "hi"},
                    headers={"X-Username": "alice"})
    assert r.status_code == 201


def test_gold_post_to_board_sets_board_from_url(client, alice):
    # The URL parameter should be what ends up on the row.
    body = client.post("/boards/tech/posts", json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    assert body["board"] == "tech"


def test_gold_post_to_board_response_shape(client, alice):
    body = client.post("/boards/tech/posts", json={"message": "hi"},
                       headers={"X-Username": "alice"}).json()
    assert set(body.keys()) == {
        "id", "username", "message", "created_at", "updated_at", "board",
    }


def test_gold_post_to_board_missing_x_username_returns_400(client, alice):
    # Same X-Username contract as POST /posts.  This endpoint is a
    # wrapper; the auth semantics must be identical, or we'd end up
    # with two subtly different doors into the same write.
    r = client.post("/boards/tech/posts", json={"message": "hi"})
    assert r.status_code == 400


def test_gold_post_to_board_unknown_user_returns_404(client):
    r = client.post("/boards/tech/posts", json={"message": "hi"},
                    headers={"X-Username": "ghost"})
    assert r.status_code == 404


def test_gold_post_to_board_invalid_board_returns_422(client, alice):
    # URL path {name} must obey the same regex as the body `board`.
    # A user-supplied path param that fails validation → 422.
    r = client.post("/boards/has spaces/posts", json={"message": "hi"},
                    headers={"X-Username": "alice"})
    # httpx/TestClient URL-encodes the space — but the server still
    # sees "has spaces" after decoding, and it still fails validation.
    assert r.status_code == 422


def test_gold_post_to_board_appears_in_board_listing(client, alice):
    # End-to-end: POST via the convenience endpoint must become
    # visible through the board listing.  Catches a mistake where
    # the two paths write to different storage.
    client.post("/boards/tech/posts", json={"message": "hi"},
                headers={"X-Username": "alice"})
    assert any(b["name"] == "tech" for b in client.get("/boards").json())


def test_gold_post_to_board_body_message_required(client, alice):
    # Body is still {"message": str}.  Omitting message → 422.
    r = client.post("/boards/tech/posts", json={},
                    headers={"X-Username": "alice"})
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════
#  GOLD — GET /posts?board=X  (filter, composes with everything else)
# ══════════════════════════════════════════════════════════════════════

def test_gold_filter_by_board(client, alice):
    client.post("/posts", json={"message": "t", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "m", "board": "music"},
                headers={"X-Username": "alice"})
    body = client.get("/posts", params={"board": "tech"}).json()
    assert len(body) == 1
    assert body[0]["board"] == "tech"


def test_gold_filter_by_unknown_board_returns_empty_array(client, alice):
    # Same filter-vs-lookup distinction as ?username=.  Unknown →
    # empty result, not an error.
    client.post("/posts", json={"message": "x"}, headers={"X-Username": "alice"})
    r = client.get("/posts", params={"board": "ghost"})
    assert r.status_code == 200
    assert r.json() == []


def test_gold_filter_by_board_composes_with_q(client, alice):
    # Intersection: tech board AND message contains "keep".
    client.post("/posts", json={"message": "keep me", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "drop me", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "keep me", "board": "music"},
                headers={"X-Username": "alice"})
    body = client.get("/posts", params={"board": "tech", "q": "keep"}).json()
    assert len(body) == 1
    assert body[0]["message"] == "keep me"
    assert body[0]["board"] == "tech"


def test_gold_filter_by_board_composes_with_username(client, alice, bob):
    # Board + username.  Both filter to alice's tech posts.
    client.post("/posts", json={"message": "a-t", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "b-t", "board": "tech"},
                headers={"X-Username": "bob"})
    client.post("/posts", json={"message": "a-m", "board": "music"},
                headers={"X-Username": "alice"})
    body = client.get("/posts",
                      params={"board": "tech", "username": "alice"}).json()
    assert len(body) == 1
    assert body[0]["message"] == "a-t"


def test_gold_filter_by_board_composes_with_pagination(client, alice):
    # Five tech posts, limit 2 → 2 tech posts.  Pagination applies
    # to the FILTERED result (not the raw posts table).
    for i in range(5):
        client.post("/posts", json={"message": f"t{i}", "board": "tech"},
                    headers={"X-Username": "alice"})
    for i in range(5):
        client.post("/posts", json={"message": f"m{i}", "board": "music"},
                    headers={"X-Username": "alice"})
    body = client.get("/posts", params={"board": "tech", "limit": 2}).json()
    assert len(body) == 2
    assert all(p["board"] == "tech" for p in body)


def test_gold_all_four_filters_compose(client, alice, bob):
    # The whole stack at once: board + username + q + limit.  If any
    # of them drop out of the SQL builder, this test catches it.
    for i in range(3):
        client.post("/posts", json={"message": f"keep {i}", "board": "tech"},
                    headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "drop", "board": "tech"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "keep 0", "board": "music"},
                headers={"X-Username": "alice"})
    client.post("/posts", json={"message": "keep 0", "board": "tech"},
                headers={"X-Username": "bob"})
    body = client.get("/posts", params={
        "board": "tech", "username": "alice", "q": "keep", "limit": 2,
    }).json()
    assert len(body) == 2
    for p in body:
        assert p["board"] == "tech"
        assert p["username"] == "alice"
        assert "keep" in p["message"]
