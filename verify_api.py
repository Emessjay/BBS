"""
verify_api.py - conformance check for your BBS webserver.

HOW TO USE:
  1. Start your server:   uvicorn main:app --port 8000
  2. In another shell:    python verify_api.py
  3. Read the output. Fix any FAIL lines. Repeat.

This script uses random usernames on every run, so it does NOT require
a clean database. You can run it over and over against the same server.
If you want to start fresh, stop your server, delete bbs.db, and
restart.

THREE SECTIONS ARE MARKED 'STUDENT TODO'. You must fill them in to
complete the bronze tier. Read the assignment PDF for the exact
behavior each section should verify.
"""

import os
import sys
import uuid

import httpx

BASE = os.environ.get("BBS_BASE", "http://localhost:8000")

# Random suffix keeps test data from colliding across runs.
RUN = uuid.uuid4().hex[:8]
ALICE = f"alice_{RUN}"
BOB = f"bob_{RUN}"
GHOST = f"ghost_{RUN}"  # never created

FAILED = 0
PASSED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILED, PASSED
    if cond:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        msg = f"FAIL  {name}"
        if detail:
            msg += f"  ({detail})"
        print(msg)


def main() -> int:
    try:
        c = httpx.Client(base_url=BASE, timeout=5.0)
        c.get("/users")
    except httpx.ConnectError:
        print(f"ERROR: could not connect to {BASE}")
        print("Is your server running? Try: uvicorn main:app --port 8000")
        return 2

    print(f"Run id: {RUN} (usernames are suffixed with this)")
    print()

    state = {}
    run_user_checks(c, state)
    run_post_checks(c, state)
    run_search_checks(c, state)

    # ==================================================================
    # STUDENT TODO #1: DELETE /posts/{id}
    #
    # Implement run_delete_checks() below. It should verify:
    #   - DELETE on an existing post returns 204
    #   - After DELETE, GET on the same id returns 404
    #   - DELETE on a post id that doesn't exist returns 404
    #
    # state["alice_post_id"] holds a post id you created earlier.
    # Use it (or create a new one to delete). Also, pick a post id
    # that is very unlikely to exist when testing the 404 case.
    #
    # When you've implemented it, uncomment the call below.
    # ==================================================================
    run_delete_checks(c, state)

    # ==================================================================
    # STUDENT TODO #2: pagination on GET /posts
    #
    # Implement run_pagination_checks() below. It should verify:
    #   - GET /posts?limit=N returns at most N items
    #   - GET /posts?offset=K skips the first K items
    #   - GET /posts?limit=0 returns 422
    #   - GET /posts?limit=500 returns 422
    #   - GET /posts?offset=-1 returns 422
    #
    # When you've implemented it, uncomment the call below.
    # ==================================================================
    run_pagination_checks(c, state)

    # ==================================================================
    # STUDENT TODO #3: exact response field shapes
    #
    # Implement run_field_shape_checks() below. It should verify that
    # your response bodies contain EXACTLY the fields the spec lists.
    # No extras, nothing missing.
    #
    # A user object (from POST /users, GET /users/{username}, and items
    # in GET /users) has exactly {username, created_at}.
    #
    # A post object (from POST /posts, GET /posts/{id}, and items in
    # GET /posts) has exactly {id, username, message, created_at}.
    #
    # An extra field like `email`, `updated_at`, or `user_id` is a FAIL.
    # A missing field is a FAIL. You will need to compare
    # set(body.keys()) against the expected set for each shape.
    #
    # Create fresh users and posts inside this function if you want
    # isolation, or reuse state["alice_post_id"] and friends.
    #
    # When you've implemented it, uncomment the call below.
    # ==================================================================
    run_field_shape_checks(c, state)

    print()
    print(f"{PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


def run_user_checks(c: httpx.Client, state: dict) -> None:
    r = c.post("/users", json={"username": ALICE})
    check("POST /users creates a user (201)", r.status_code == 201, detail=f"got {r.status_code}")
    if r.status_code == 201:
        body = r.json()
        check(
            "POST /users response has exactly username and created_at",
            set(body.keys()) == {"username", "created_at"} and body["username"] == ALICE,
            detail=str(body),
        )

    r = c.post("/users", json={"username": ALICE})
    check("POST /users duplicate returns 409", r.status_code == 409, detail=f"got {r.status_code}")

    r = c.post("/users", json={"username": "ab"})
    check("POST /users too-short username returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    r = c.post("/users", json={"username": "has spaces"})
    check("POST /users invalid chars returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    r = c.post("/users", json={})
    check("POST /users missing username returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    c.post("/users", json={"username": BOB})

    r = c.get("/users")
    check("GET /users returns 200", r.status_code == 200, detail=f"got {r.status_code}")
    if r.status_code == 200:
        usernames = [u["username"] for u in r.json()]
        check(
            "GET /users includes both created users",
            ALICE in usernames and BOB in usernames,
            detail=f"looking for {ALICE} and {BOB}",
        )

    r = c.get(f"/users/{ALICE}")
    check(f"GET /users/{ALICE} returns 200", r.status_code == 200, detail=f"got {r.status_code}")
    if r.status_code == 200:
        body = r.json()
        check(
            f"GET /users/{ALICE} body.username == {ALICE}",
            body.get("username") == ALICE,
            detail=str(body),
        )

    r = c.get(f"/users/{GHOST}")
    check(f"GET /users/{GHOST} returns 404", r.status_code == 404, detail=f"got {r.status_code}")


def run_post_checks(c: httpx.Client, state: dict) -> None:
    r = c.post("/posts", json={"message": "hello world"}, headers={"X-Username": ALICE})
    check("POST /posts with X-Username returns 201", r.status_code == 201, detail=f"got {r.status_code}")
    if r.status_code == 201:
        body = r.json()
        expected_keys = {"id", "username", "message", "created_at"}
        check(
            "POST /posts response has exactly id, username, message, created_at",
            set(body.keys()) == expected_keys,
            detail=str(body),
        )
        check("POST /posts response username matches header", body.get("username") == ALICE)
        check("POST /posts response message matches body", body.get("message") == "hello world")
        state["alice_post_id"] = body.get("id")

    r = c.post("/posts", json={"message": "hi"})
    check("POST /posts without X-Username returns 400", r.status_code == 400, detail=f"got {r.status_code}")

    r = c.post("/posts", json={"message": "hi"}, headers={"X-Username": GHOST})
    check("POST /posts with unknown user returns 404", r.status_code == 404, detail=f"got {r.status_code}")

    r = c.post("/posts", json={"message": ""}, headers={"X-Username": ALICE})
    check("POST /posts with empty message returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    r = c.post(
        "/posts",
        json={"message": "x" * 501},
        headers={"X-Username": ALICE},
    )
    check("POST /posts with 501-char message returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    r = c.post("/posts", json={}, headers={"X-Username": ALICE})
    check("POST /posts missing message returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    r = c.post("/posts", json={"message": "second post"}, headers={"X-Username": BOB})
    if r.status_code == 201:
        state["bob_post_id"] = r.json().get("id")

    r = c.get("/posts")
    check("GET /posts returns 200", r.status_code == 200, detail=f"got {r.status_code}")
    if r.status_code == 200:
        posts = r.json()
        check(
            "GET /posts returns a JSON array",
            isinstance(posts, list),
            detail=f"got {type(posts).__name__}",
        )

    if "alice_post_id" in state:
        pid = state["alice_post_id"]
        r = c.get(f"/posts/{pid}")
        check(f"GET /posts/{pid} (alice's post) returns 200", r.status_code == 200, detail=f"got {r.status_code}")

    r = c.get("/posts/99999999")
    check("GET /posts/99999999 returns 404", r.status_code == 404, detail=f"got {r.status_code}")

    r = c.get(f"/users/{ALICE}/posts")
    check(f"GET /users/{ALICE}/posts returns 200", r.status_code == 200, detail=f"got {r.status_code}")
    if r.status_code == 200:
        alice_posts = r.json()
        check(
            f"GET /users/{ALICE}/posts contains only {ALICE}'s posts",
            all(p.get("username") == ALICE for p in alice_posts) and len(alice_posts) >= 1,
            detail=str(alice_posts),
        )

    r = c.get(f"/users/{GHOST}/posts")
    check(f"GET /users/{GHOST}/posts returns 404", r.status_code == 404, detail=f"got {r.status_code}")


def run_search_checks(c: httpx.Client, state: dict) -> None:
    needle = f"needle_{RUN}"
    c.post("/posts", json={"message": f"a post with {needle} in it"}, headers={"X-Username": ALICE})
    c.post("/posts", json={"message": "nothing to see"}, headers={"X-Username": ALICE})

    r = c.get("/posts", params={"q": needle})
    check(f"GET /posts?q={needle} returns 200", r.status_code == 200, detail=f"got {r.status_code}")
    if r.status_code == 200:
        matches = r.json()
        check(
            f"GET /posts?q={needle} returns only matching posts",
            all(needle in p.get("message", "") for p in matches) and len(matches) >= 1,
            detail=str(matches),
        )


def run_delete_checks(c: httpx.Client, state: dict) -> None:
    # We create a throw-away post specifically for this test instead
    # of reusing state["alice_post_id"].  That keeps delete behaviour
    # isolated from anything else in the run — if a later section ever
    # assumes alice's first post is still there, we do not break it.
    r = c.post("/posts", json={"message": "doomed"}, headers={"X-Username": ALICE})
    check(
        "DELETE setup: created a post to delete",
        r.status_code == 201,
        detail=f"got {r.status_code}",
    )
    if r.status_code != 201:
        return   # nothing to delete; skip the rest
    pid = r.json()["id"]

    # Happy path: DELETE on an existing post is 204 (no content).
    r = c.delete(f"/posts/{pid}")
    check(f"DELETE /posts/{pid} returns 204", r.status_code == 204, detail=f"got {r.status_code}")

    # After DELETE, GET on the same id must be 404.
    r = c.get(f"/posts/{pid}")
    check(
        f"GET /posts/{pid} after DELETE returns 404",
        r.status_code == 404,
        detail=f"got {r.status_code}",
    )

    # DELETE on an id that doesn't exist is also 404.  We pick an id
    # that is absurdly high to stay clear of anything another test
    # could plausibly create.
    r = c.delete("/posts/99999999")
    check(
        "DELETE /posts/99999999 (missing) returns 404",
        r.status_code == 404,
        detail=f"got {r.status_code}",
    )


def run_pagination_checks(c: httpx.Client, state: dict) -> None:
    # Seed enough posts for limit/offset math to be meaningful.  Five
    # is enough to distinguish limit=2, offset=2, and offset beyond
    # the end, without flooding the DB.
    for i in range(5):
        c.post(
            "/posts",
            json={"message": f"pagination seed {RUN} {i}"},
            headers={"X-Username": ALICE},
        )

    # ?limit=N returns at most N items.  Using limit=2 and a corpus of
    # >= 5 means we expect exactly 2 back.
    r = c.get("/posts", params={"limit": 2})
    check(
        "GET /posts?limit=2 returns at most 2 items",
        r.status_code == 200 and len(r.json()) <= 2,
        detail=f"status {r.status_code}, len {len(r.json()) if r.status_code == 200 else '?'}",
    )

    # ?offset=K skips the first K items.  We compare the id-sets of
    # the full response to the offset response — the offset call must
    # leave behind exactly K ids from the top.
    full = c.get("/posts", params={"limit": 100})
    shifted = c.get("/posts", params={"limit": 100, "offset": 2})
    if full.status_code == 200 and shifted.status_code == 200:
        full_ids = {p["id"] for p in full.json()}
        shift_ids = {p["id"] for p in shifted.json()}
        missing = full_ids - shift_ids
        check(
            "GET /posts?offset=2 skips exactly 2 items",
            len(missing) == 2,
            detail=f"skipped {len(missing)} (expected 2)",
        )
    else:
        check("GET /posts?offset setup requests both returned 200", False,
              detail=f"full={full.status_code}, shifted={shifted.status_code}")

    # Out-of-range validation.  These are produced by Pydantic's
    # Query(ge=1, le=200) and Query(ge=0) — so 422, not 400.
    r = c.get("/posts", params={"limit": 0})
    check("GET /posts?limit=0 returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    r = c.get("/posts", params={"limit": 500})
    check("GET /posts?limit=500 returns 422", r.status_code == 422, detail=f"got {r.status_code}")

    r = c.get("/posts", params={"offset": -1})
    check("GET /posts?offset=-1 returns 422", r.status_code == 422, detail=f"got {r.status_code}")


def run_field_shape_checks(c: httpx.Client, state: dict) -> None:
    # We create fresh entities inside this function so we're asserting
    # on KNOWN responses — not responses that earlier sections might
    # have dirtied with extra fields.  Every assertion below is
    # SET-EQUALITY, so an extra field fails just as loudly as a
    # missing one.

    expected_user = {"username", "created_at"}
    expected_post = {"id", "username", "message", "created_at"}

    # ── User shape: POST, GET one, and items in GET all ───────────
    fresh_user = f"shape_{RUN}"
    r = c.post("/users", json={"username": fresh_user})
    if r.status_code == 201:
        body = r.json()
        check(
            f"POST /users response has exactly {expected_user}",
            set(body.keys()) == expected_user,
            detail=str(body),
        )

    r = c.get(f"/users/{fresh_user}")
    if r.status_code == 200:
        body = r.json()
        check(
            f"GET /users/{{username}} response has exactly {expected_user}",
            set(body.keys()) == expected_user,
            detail=str(body),
        )

    r = c.get("/users")
    if r.status_code == 200 and isinstance(r.json(), list) and r.json():
        # Pick the item we just created so we're asserting on a known
        # row, not whatever happened to land first.
        item = next((u for u in r.json() if u.get("username") == fresh_user), r.json()[0])
        check(
            f"GET /users item has exactly {expected_user}",
            set(item.keys()) == expected_user,
            detail=str(item),
        )

    # ── Post shape: POST, GET one, and items in GET all ───────────
    r = c.post(
        "/posts",
        json={"message": f"shape check {RUN}"},
        headers={"X-Username": fresh_user},
    )
    if r.status_code == 201:
        body = r.json()
        check(
            f"POST /posts response has exactly {expected_post}",
            set(body.keys()) == expected_post,
            detail=str(body),
        )
        pid = body["id"]

        r = c.get(f"/posts/{pid}")
        if r.status_code == 200:
            body = r.json()
            check(
                f"GET /posts/{{id}} response has exactly {expected_post}",
                set(body.keys()) == expected_post,
                detail=str(body),
            )

        r = c.get("/posts")
        if r.status_code == 200 and isinstance(r.json(), list) and r.json():
            item = next((p for p in r.json() if p.get("id") == pid), r.json()[0])
            check(
                f"GET /posts item has exactly {expected_post}",
                set(item.keys()) == expected_post,
                detail=str(item),
            )


if __name__ == "__main__":
    sys.exit(main())
