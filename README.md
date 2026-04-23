# JBBS - Jack's Bulletin Board System

## How to Run

**Dependencies:** Python 3.12+ (stdlib only — no third-party packages). No `pip install` needed. The database (`bbs.db`) and JSON file (`bbs.json`) are created automatically on first use.

### Part A — JSON Version (`bbs.py`)

| Command | Description |
|---------|-------------|
| `python bbs.py post <user> <message>` | Post to the default `general` board |
| `python bbs.py post <user> <board> <message>` | Post to a specific board |
| `python bbs.py read` | Read all posts across all boards |
| `python bbs.py read <board>` | Read posts from a specific board |
| `python bbs.py boards` | List all boards with post counts |
| `python bbs.py users` | List all users in first-appearance order |
| `python bbs.py search <keyword>` | Search posts by keyword (case-insensitive) |

### Part B — SQLite Version (`bbs_db.py`)

All Part A commands, plus:

| Command | Description |
|---------|-------------|
| `python bbs_db.py register` | Create a new account with a password |
| `python bbs_db.py login` | Log in and enter an interactive `jbbs>` session |

### Part C — Migration (`migrate.py`)

| Command | Description |
|---------|-------------|
| `python migrate.py` | Read `bbs.json` and populate `bbs.db` |

### Testing

```bash
python -m pytest test_bbs.py -v
```

32 end-to-end tests covering all three parts. Each test runs the program under test as a subprocess, strips ANSI codes for clean assertions, and is fully self-contained — an autouse fixture wipes `bbs.json`, `bbs.db`, and any backup files before and after every test, so tests can run in any order.

## Tier

**Gold.**

## Search Comparison

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

## Silver: Boards

Posts belong to named boards. The default board is `general`, but you can target any board:

```bash
python bbs.py post alice tech "SQLite is great"
python bbs.py read tech             # read only that board
python bbs.py boards                # list boards with post counts
```

In both versions, the board is stored as a field on each post. The JSON version keeps it as a `"board"` key on each post object; the SQLite version stores it as a `board TEXT NOT NULL` column on a single `posts` table. That means:

- `read <board>` is one `WHERE board = ?` query — no table enumeration, no `UNION ALL`.
- `boards` is one `SELECT board, COUNT(*) FROM posts GROUP BY board` — no scanning `sqlite_master`.
- "all posts by user X" is a plain `JOIN` instead of N queries across N board tables.

Board names are validated (letters/digits/underscores only, max 32 chars) at the input boundary via `validate_board()`. This is no longer a SQL-injection guard (board values go through `?` placeholders) — it just keeps junk like empty strings or whitespace out of the data.

## Gold: ASCII Art, Colored Output, and Persistent Logins

### ASCII Art & Colored Output

Both versions display an ASCII art banner (block-letter "JBBS" using box-drawing characters) when run with no arguments. All output uses a consistent ANSI 256-color palette — lime green for usernames and success messages, purple for timestamps and borders, dim text for secondary info. This gives it the retro-terminal feel of an actual BBS.

### User Accounts & Interactive Sessions

The SQLite version adds `register` and `login` commands. Passwords are hashed with PBKDF2-HMAC-SHA256 (100,000 iterations, 16-byte random salt) using only the stdlib — no bcrypt dependency needed. Hashes are stored as `salt_hex:key_hex` in the `password_hash` column of the `users` table. Verification uses `hmac.compare_digest` so the check runs in constant time (no timing side-channel on wrong-password guesses).

Users created via CLI posts (before registering) have a `NULL` password hash. Running `register` with that username lets you "claim" the account by setting a password, so no posts are lost.

After logging in, you enter a persistent `jbbs>` prompt — an interactive REPL where your username is implicit:

```
jbbs> post Hello everyone!              # posts as you, to general
jbbs> post #tech Check this out         # posts to the tech board
jbbs> read
jbbs> boards
jbbs> search Hello
jbbs> whoami
jbbs> quit
```
