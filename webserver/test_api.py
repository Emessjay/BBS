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
# object must have EXACTLY {username, created_at} — not a subset, not
# a superset.  These two sets are the source of truth for that check.
# The verifier's STUDENT TODO #3 compares set(body.keys()) against
# these, and so do we.
USER_FIELDS = {"username", "created_at"}
POST_FIELDS = {"id", "username", "message", "created_at"}

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
