# BBS - Bulletin Board System

**Tier: Gold**

A terminal-based bulletin board system built in two versions: a JSON-backed prototype (`bbs.py`) and a SQLite-backed upgrade (`bbs_db.py`) with user authentication and an interactive session mode. A migration script (`migrate.py`) bridges the two.

## Setup & Running

**Dependencies:** Python 3.12+ (stdlib only — no third-party packages).

```bash
# Part A — JSON version
python bbs.py post alice "Hello, is anyone out there?"
python bbs.py post bob general "Posting to the general board"
python bbs.py read
python bbs.py read general          # read a specific board
python bbs.py boards                # list all boards
python bbs.py users
python bbs.py search "Hello"

# Part B — SQLite version (same commands, plus auth)
python bbs_db.py post alice "Hello from the database side"
python bbs_db.py post alice tech "SQL is neat"
python bbs_db.py read
python bbs_db.py boards
python bbs_db.py users
python bbs_db.py search "Hello"

# Gold — register and log in to an interactive session
python bbs_db.py register
python bbs_db.py login

# Part C — migrate JSON data into SQLite
python migrate.py
```

No `pip install` needed. The database (`bbs.db`) and JSON file (`bbs.json`) are created automatically on first use.

## Search: JSON vs. SQL

In the JSON version, search loads the entire `bbs.json` file into memory as a Python list, then iterates over every post and checks whether the keyword appears in the message using a case-insensitive string comparison (`keyword.lower() in message.lower()`). Every single post is deserialized and examined regardless of whether it matches. The work scales linearly with the total number of posts — O(n) — and it all happens in the application layer.

The SQLite version pushes that work into the database engine. A single SQL query with `WHERE message LIKE ?` tells SQLite to scan and filter internally, returning only the rows that match. The application never sees non-matching posts. For a small BBS the difference is negligible, but at a million posts it would matter significantly: the JSON version would need to parse a massive file into memory on every search (the entire million-post JSON array), while the SQLite version reads only the relevant pages from disk. SQLite could also benefit from indexing (e.g., FTS5 full-text search) to avoid a full table scan entirely — something flat JSON has no equivalent for.

## Migration Behavior

When `migrate.py` runs and `bbs.db` already contains data, it:

1. **Backs up** the existing database to `bbs_backup_<timestamp>.db`
2. **Reads out** all existing DB posts into memory
3. **Wipes** the database tables and recreates them from scratch
4. **Merges** the JSON posts and the existing DB posts into a single list
5. **Re-inserts** everything sorted chronologically, so post IDs always increase with time (ID 1 = the earliest post)

I chose merge-and-reorder over "skip duplicates" or "error out" because it's the most predictable: you always end up with a complete, chronologically consistent database. The backup ensures nothing is lost if you run the migration by accident. The tradeoff is that running `migrate.py` twice will duplicate the JSON posts (the first run's copies get read back from the DB and merged with the JSON originals again), but the backup file means you can always recover the previous state.

If `bbs.json` doesn't exist or is empty, the script prints a message and exits — it won't touch the database.

## Silver Feature: Boards

Posts belong to named boards. The default board is `general`, but you can target any board:

```bash
python bbs.py post alice tech "SQLite is great"
python bbs.py read tech             # read only that board
python bbs.py boards                # list boards with post counts
```

In the JSON version, boards are stored as a `"board"` field on each post object. In the SQLite version, each board gets its own table (`board_general`, `board_tech`, etc.). This means reading a single board is a direct table scan with no filtering — and the `boards` command queries `sqlite_master` to discover all board tables dynamically.

Board names are validated with a regex (`^[a-zA-Z0-9_]+$`) before being interpolated into SQL, since table names can't use parameterized placeholders.

## Gold Features

### ASCII Art & Colored Output

Both versions display an ASCII art banner (block-letter "BBS" using box-drawing characters) when run with no arguments. All output uses a consistent ANSI 256-color palette — lime green for usernames and success messages, purple for timestamps and borders, dim text for secondary info. This gives it the retro-terminal feel of an actual BBS.

### User Accounts & Interactive Sessions

The SQLite version adds `register` and `login` commands. Passwords are hashed with PBKDF2-HMAC-SHA256 (100,000 iterations, 16-byte random salt) using only the stdlib — no bcrypt dependency needed. Hashes are stored as `salt_hex:key_hex` in the `password_hash` column of the `users` table.

Users created via CLI posts (before registering) have a `NULL` password hash. Running `register` with that username lets you "claim" the account by setting a password, so no posts are lost.

After logging in, you enter a persistent `bbs>` prompt — an interactive REPL where your username is implicit:

```
bbs> post Hello everyone!              # posts as you, to general
bbs> post #tech Check this out         # posts to the tech board
bbs> read
bbs> boards
bbs> search Hello
bbs> whoami
bbs> quit
```

This required adding the `password_hash` column to the `users` table. The schema auto-migrates: if an older database lacks the column, `init_db()` adds it with `ALTER TABLE` on startup, so nothing breaks.

## Testing

The test suite (`test_bbs.py`) covers all three parts with 31 end-to-end tests:

```bash
python -m pytest test_bbs.py -v
```

Tests run each program as a subprocess (simulating real CLI usage), strip ANSI codes from output for clean assertions, and clean up generated files automatically.
