"""Local SQLite archive of every scored paper (the source of truth for the website)."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from dataclasses import asdict

from .fetch import Paper

DB_PATH = "data/digest.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT NOT NULL, abstract TEXT,
  authors TEXT, affiliations TEXT, categories TEXT, tags TEXT,
  announce_type TEXT, link TEXT, code_url TEXT,
  interest REAL, quality REAL DEFAULT 5, impact REAL DEFAULT 0, quality_note TEXT DEFAULT '', final REAL, reason TEXT, tldr TEXT, why TEXT,
  author_hits TEXT, inst_hits TEXT
);
CREATE INDEX IF NOT EXISTS papers_date ON papers(date);
CREATE TABLE IF NOT EXISTS ratings (
  id TEXT PRIMARY KEY, rating INTEGER NOT NULL, rated_at TEXT
);
CREATE TABLE IF NOT EXISTS bookmarks (
  id TEXT PRIMARY KEY, saved_at TEXT
);
CREATE TABLE IF NOT EXISTS likes (
  id TEXT PRIMARY KEY, liked_at TEXT
);
CREATE TABLE IF NOT EXISTS vectors (
  id TEXT PRIMARY KEY, vec BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS zotero (
  id TEXT PRIMARY KEY, item_key TEXT NOT NULL, pushed_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY, v TEXT
);
CREATE TABLE IF NOT EXISTS days (
  date TEXT PRIMARY KEY, total INTEGER, cost REAL, calls INTEGER
);
CREATE TABLE IF NOT EXISTS explanations (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, text TEXT, model TEXT, cost REAL, at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS explain_costs (
  at TEXT NOT NULL, id TEXT NOT NULL, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost REAL
);
CREATE TABLE IF NOT EXISTS visits (
  date TEXT NOT NULL, visitor TEXT NOT NULL, pages INTEGER DEFAULT 0, actions INTEGER DEFAULT 0,
  PRIMARY KEY (date, visitor)
);
CREATE TABLE IF NOT EXISTS users (
  name TEXT PRIMARY KEY, pw TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS user_ratings (
  user TEXT NOT NULL, id TEXT NOT NULL, rating INTEGER NOT NULL, rated_at TEXT, PRIMARY KEY (user, id)
);
CREATE TABLE IF NOT EXISTS user_bookmarks (
  user TEXT NOT NULL, id TEXT NOT NULL, saved_at TEXT, PRIMARY KEY (user, id)
);
CREATE TABLE IF NOT EXISTS user_likes (
  user TEXT NOT NULL, id TEXT NOT NULL, liked_at TEXT, PRIMARY KEY (user, id)
);
"""
JSON_COLS = (
    "authors",
    "affiliations",
    "categories",
    "tags",
    "author_hits",
    "inst_hits",
)


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # wait up to 30 s for another writer (the daily run, another reader's click) instead of
    # failing with "database is locked" after the default 5 s
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    # WAL: readers (the site) never block the writer and vice versa. Stored in the file, so
    # this is a no-op after the first time; skipped if another connection holds a lock.
    try:
        if con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    con.executescript(SCHEMA)
    have = {r["name"] for r in con.execute("PRAGMA table_info(papers)")}
    for col, ddl in (
        ("quality", "REAL DEFAULT 5"),
        ("quality_note", "TEXT DEFAULT ''"),
        ("impact", "REAL DEFAULT 0"),
    ):
        if col not in have:  # databases created before these scores
            con.execute(f"ALTER TABLE papers ADD COLUMN {col} {ddl}")
    return con


def upsert(
    con: sqlite3.Connection, papers: list[Paper], date: str, stats: dict | None = None
) -> None:
    with con:
        for p in papers:
            d = asdict(p)
            d.pop("intro", None)
            d["date"] = date
            for c in JSON_COLS:
                d[c] = json.dumps(d[c], ensure_ascii=False)
            cols = list(d)
            con.execute(
                f"INSERT OR REPLACE INTO papers ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [d[c] for c in cols],
            )
        if stats:
            con.execute(
                "INSERT OR REPLACE INTO days VALUES (?,?,?,?)",
                (date, stats.get("total"), stats.get("cost"), stats.get("calls")),
            )
        con.execute(  # "Updated …" on the site
            "INSERT OR REPLACE INTO meta VALUES ('updated_at', ?)",
            (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),),
        )
        bump_papers_rev(con)


def bump_papers_rev(con: sqlite3.Connection) -> None:
    """Mark papers/days as changed, so the local server rebuilds its index (likes and ratings
    don't touch it). Call inside the writing transaction."""
    con.execute(
        "INSERT INTO meta VALUES ('papers_rev', '1') "
        "ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1"
    )


def updated_at(con: sqlite3.Connection) -> str:
    """When papers were last written (UTC ISO 8601), or "" for a database from before this."""
    r = con.execute("SELECT v FROM meta WHERE k = 'updated_at'").fetchone()
    return r[0] if r else ""


def papers_rev(con: sqlite3.Connection) -> tuple:
    """Changes whenever papers or days change (O(1), unlike counting rows)."""
    r = con.execute("SELECT v FROM meta WHERE k = 'papers_rev'").fetchone()
    return (r[0] if r else None, con.execute("SELECT MAX(rowid) FROM papers").fetchone()[0])


INDEX_COLS = (
    "id, date, title, final, interest, tags, authors, author_hits, inst_hits, code_url, "
    "categories, quality, impact"
)


def all_papers(con: sqlite3.Connection, date: str | None = None, cols: str = "*") -> list[dict]:
    """Every paper (or one day's), newest first. `cols` limits the columns read, e.g.
    INDEX_COLS for the site index, which never needs abstracts or summaries."""
    rows = con.execute(
        f"SELECT {cols} FROM papers"
        + (" WHERE date = ?" if date else "")
        + " ORDER BY date DESC, final DESC, interest DESC",
        (date,) if date else (),
    )
    out = []
    for r in rows:
        d = dict(r)
        for c in JSON_COLS:
            if c in d:
                d[c] = json.loads(d[c] or "[]")
        out.append(d)
    return out


def get_paper(con: sqlite3.Connection, pid: str) -> dict | None:
    r = con.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for c in JSON_COLS:
        d[c] = json.loads(d[c] or "[]")
    return d


def load_day(con: sqlite3.Connection, date: str) -> list[Paper]:
    """Papers stored for one day, as fresh Paper objects (scores reset by re-scoring)."""
    out = []
    for r in con.execute("SELECT * FROM papers WHERE date = ?", (date,)):
        d = dict(r)
        d.pop("date")
        for c in JSON_COLS:
            d[c] = json.loads(d[c] or "[]")
        out.append(
            Paper(**{k: v for k, v in d.items() if k in Paper.__dataclass_fields__})
        )
    return out


def import_ratings(con: sqlite3.Connection, path: str) -> int:
    """Load a ratings.json exported from the website:
    {"ratings": {"2610.01234": 5, ...}, "bookmarks": ["2610.01234", ...]}.
    A rating of 0 removes the rating. Bookmarks are only ever added here (unbookmark on the
    local site)."""
    with open(path) as f:
        data = json.load(f)
    ratings = data.get("ratings", data) if "ratings" in data or "bookmarks" not in data else {}
    now = dt.date.today().isoformat()
    n = 0
    with con:
        for pid in data.get("bookmarks", []):
            con.execute("INSERT OR IGNORE INTO bookmarks VALUES (?,?)", (pid, now))
            n += 1
        for pid, r in ratings.items():
            r = int(r)
            if r <= 0:
                con.execute("DELETE FROM ratings WHERE id=?", (pid,))
            elif 1 <= r <= 5:
                con.execute(
                    "INSERT OR REPLACE INTO ratings VALUES (?,?,?)", (pid, r, now)
                )
            else:
                continue
            n += 1
    return n


def _picks(kind: str, user: str | None) -> tuple[str, str, tuple]:
    """(table, WHERE clause, params) for "ratings" | "bookmarks" | "likes": the shared (lab)
    picks when `user` is None, else that logged-in user's own (user_* tables, user column first)."""
    return (f"user_{kind}", "user = ?", (user,)) if user else (kind, "1", ())


def ratings(con: sqlite3.Connection, user: str | None = None) -> dict[str, int]:
    t, w, p = _picks("ratings", user)
    return {r["id"]: r["rating"] for r in con.execute(f"SELECT id, rating FROM {t} WHERE {w}", p)}


def liked_titles(
    con: sqlite3.Connection, min_rating: int = 4, limit: int = 30
) -> list[str]:
    """Titles of papers you liked, bookmarked or rated highly (newest first) → extra positive
    examples for scoring. A bookmark you also rated below min_rating doesn't count."""
    rows = con.execute(
        "SELECT p.title FROM papers p JOIN ("
        "  SELECT id, MAX(at) AS at FROM ("
        "    SELECT id, rated_at AS at FROM ratings WHERE rating >= ?"
        "    UNION ALL SELECT id, liked_at FROM likes"
        "    UNION ALL SELECT id, saved_at FROM bookmarks"
        "      WHERE id NOT IN (SELECT id FROM ratings WHERE rating < ?)"
        "  ) GROUP BY id"
        ") x ON x.id = p.id ORDER BY x.at DESC, p.date DESC LIMIT ?",
        (min_rating, min_rating, limit),
    )
    return [r["title"] for r in rows]


def set_rating(con: sqlite3.Connection, pid: str, rating: int, user: str | None = None) -> None:
    t, w, p = _picks("ratings", user)
    with con:
        if rating <= 0:
            con.execute(f"DELETE FROM {t} WHERE {w} AND id=?", p + (pid,))
        elif rating <= 5:
            con.execute(
                f"INSERT OR REPLACE INTO {t} VALUES ({'?,' * len(p)}?,?,?)",
                p + (pid, rating, dt.date.today().isoformat()),
            )


def _set_pick(con: sqlite3.Connection, kind: str, pid: str, on: bool, user: str | None) -> None:
    t, w, p = _picks(kind, user)
    with con:
        if on:
            con.execute(
                f"INSERT OR IGNORE INTO {t} VALUES ({'?,' * len(p)}?,?)",
                p + (pid, dt.date.today().isoformat()),
            )
        else:
            con.execute(f"DELETE FROM {t} WHERE {w} AND id=?", p + (pid,))


def set_bookmark(con: sqlite3.Connection, pid: str, on: bool, user: str | None = None) -> None:
    _set_pick(con, "bookmarks", pid, on, user)


def set_like(con: sqlite3.Connection, pid: str, on: bool, user: str | None = None) -> None:
    _set_pick(con, "likes", pid, on, user)


def likes(con: sqlite3.Connection, user: str | None = None) -> list[str]:
    t, w, p = _picks("likes", user)
    return [r["id"] for r in con.execute(f"SELECT id FROM {t} WHERE {w}", p)]


def bookmarks(con: sqlite3.Connection, user: str | None = None) -> list[str]:
    t, w, p = _picks("bookmarks", user)
    return [r["id"] for r in con.execute(f"SELECT id FROM {t} WHERE {w}", p)]


# ── accounts (delivery.login) ────────────────────────────────────────────
# Logged out, picks are the lab's shared ones (tables ratings/likes/bookmarks); logged in, they
# are the user's own (user_* tables). Passwords are salted PBKDF2 hashes; a session cookie
# holds a random token, of which only the SHA-256 is stored.
_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")
MIN_PASSWORD = 6


def _hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def _check_pw(pw: str, stored: str) -> bool:
    _, salt, _h = stored.split("$")
    return hmac.compare_digest(_hash_pw(pw, bytes.fromhex(salt)), stored)


def _valid_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")


def add_user(con: sqlite3.Connection, name: str, pw: str) -> str:
    """Create an account; returns the name as stored (lower case). ValueError if it's taken or bad."""
    name = name.strip().lower()
    if not _NAME.fullmatch(name):
        raise ValueError("name: 1–32 letters, digits, '.', '_' or '-'")
    _valid_password(pw)
    try:
        with con:
            con.execute("INSERT INTO users VALUES (?,?,?)", (name, _hash_pw(pw), _now()))
    except sqlite3.IntegrityError:
        raise ValueError(f"the name {name!r} is taken") from None
    return name


def check_password(con: sqlite3.Connection, name: str, pw: str) -> str | None:
    """The user's name if the password is right, else None."""
    name = name.strip().lower()
    r = con.execute("SELECT pw FROM users WHERE name = ?", (name,)).fetchone()
    return name if r and _check_pw(pw, r["pw"]) else None


def set_password(con: sqlite3.Connection, name: str, pw: str) -> None:
    """Reset a password (run.py --set-password); logs the user out everywhere."""
    _valid_password(pw)
    with con:
        if not con.execute("UPDATE users SET pw = ? WHERE name = ?", (_hash_pw(pw), name)).rowcount:
            raise ValueError(f"no user {name!r}")
        con.execute("DELETE FROM sessions WHERE user = ?", (name,))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(con: sqlite3.Connection, name: str) -> str:
    token = secrets.token_urlsafe(32)
    with con:
        con.execute("INSERT INTO sessions VALUES (?,?,?)", (_token_hash(token), name, _now()))
    return token


def session_user(con: sqlite3.Connection, token: str) -> str | None:
    if not token:
        return None
    r = con.execute("SELECT user FROM sessions WHERE token = ?", (_token_hash(token),)).fetchone()
    return r["user"] if r else None


def end_session(con: sqlite3.Connection, token: str) -> None:
    with con:
        con.execute("DELETE FROM sessions WHERE token = ?", (_token_hash(token),))


def user_report(con: sqlite3.Connection) -> str:
    rows = con.execute(
        "SELECT u.name, u.created_at,"
        " (SELECT COUNT(*) FROM user_ratings WHERE user = u.name),"
        " (SELECT COUNT(*) FROM user_likes WHERE user = u.name),"
        " (SELECT COUNT(*) FROM user_bookmarks WHERE user = u.name)"
        " FROM users u ORDER BY u.created_at"
    ).fetchall()
    if not rows:
        return "no accounts yet"
    out = ["name                              created              rated  liked  saved"]
    out += [f"{n:32s}  {(c or '')[:19]:19s}  {r:5d}  {l:5d}  {b:5d}" for n, c, r, l, b in rows]
    return "\n".join(out)


def prune(con: sqlite3.Connection, max_papers: int) -> int:
    """Keep at most `max_papers` papers, deleting the oldest first (FIFO by date).
    Papers anyone rated, liked, bookmarked (shared or in an account) or had explained are never deleted and don't count
    toward the limit."""
    if not max_papers or max_papers <= 0:
        return 0
    special = ("id IN (SELECT id FROM ratings UNION SELECT id FROM bookmarks UNION SELECT id FROM likes"
               " UNION SELECT id FROM user_ratings UNION SELECT id FROM user_bookmarks"
               " UNION SELECT id FROM user_likes UNION SELECT id FROM explanations WHERE status = 'done')")
    n = con.execute(f"SELECT COUNT(*) FROM papers WHERE NOT ({special})").fetchone()[0]
    excess = n - max_papers
    if excess <= 0:
        return 0
    with con:
        con.execute(
            f"DELETE FROM papers WHERE id IN (SELECT id FROM papers WHERE NOT ({special}) "
            "ORDER BY date ASC, final ASC LIMIT ?)",
            (excess,),
        )
        con.execute("DELETE FROM vectors WHERE id NOT IN (SELECT id FROM papers)")
        con.execute(
            "DELETE FROM days WHERE date NOT IN (SELECT DISTINCT date FROM papers)"
        )
        bump_papers_rev(con)
    return excess


# ── paper explanations (digest/explain.py) ───────────────────────────────
# One row per paper: status pending → done | failed. explain_costs logs every call's cost, so
# the total survives re-explaining a paper.
def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def claim_explanation(con: sqlite3.Connection, pid: str, stale_minutes: int = 15, force: bool = False) -> bool:
    """Mark a paper's explanation as in progress. False if it is already done, or already being
    written (two clicks at once must not pay twice). A failed one, or one pending for longer
    than `stale_minutes` (the server stopped mid-way), can be claimed again; `force` also
    re-explains a done one."""
    stale = (dt.datetime.now() - dt.timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")
    with con:
        cur = con.execute(
            "INSERT INTO explanations (id, status, at) VALUES (?, 'pending', ?) "
            "ON CONFLICT(id) DO UPDATE SET status = 'pending', at = excluded.at, error = NULL "
            "WHERE explanations.status = 'failed' OR ? "
            "OR (explanations.status = 'pending' AND explanations.at < ?)",
            (pid, _now(), force, stale),
        )
    return cur.rowcount == 1


def save_explanation(
    con: sqlite3.Connection, pid: str, text: str, model: str, input_tokens: int, output_tokens: int, cost: float
) -> None:
    now = _now()
    with con:
        con.execute(
            "INSERT OR REPLACE INTO explanations VALUES (?, 'done', ?, ?, ?, ?, NULL)",
            (pid, text, model, cost, now),
        )
        con.execute(
            "INSERT INTO explain_costs VALUES (?,?,?,?,?,?)",
            (now, pid, model, input_tokens, output_tokens, cost),
        )


def fail_explanation(con: sqlite3.Connection, pid: str, error: str) -> None:
    with con:
        con.execute(
            "UPDATE explanations SET status = 'failed', error = ?, at = ? WHERE id = ?",
            (error[:500], _now(), pid),
        )


def get_explanation(con: sqlite3.Connection, pid: str) -> dict | None:
    r = con.execute("SELECT * FROM explanations WHERE id = ?", (pid,)).fetchone()
    return dict(r) if r else None


def explained(con: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in con.execute("SELECT id FROM explanations WHERE status = 'done'")]


def log_visit(con: sqlite3.Connection, ip: str, action: bool = False) -> None:
    """Count a page load (or a rating/like/bookmark/explain click) by this visitor today (KST).
    Only a salted hash of the IP is kept: enough to count distinct visitors, not to list them.
    The salt lives in this database, so hashes can't be matched across databases."""
    from zoneinfo import ZoneInfo

    with con:
        con.execute("INSERT OR IGNORE INTO meta VALUES ('visit_salt', ?)", (secrets.token_hex(16),))
        salt = con.execute("SELECT v FROM meta WHERE k = 'visit_salt'").fetchone()[0]
        who = hashlib.sha256((salt + ip).encode()).hexdigest()[:16]
        day = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        col = "actions" if action else "pages"
        con.execute(
            f"INSERT INTO visits (date, visitor, {col}) VALUES (?, ?, 1) "
            f"ON CONFLICT(date, visitor) DO UPDATE SET {col} = {col} + 1",
            (day, who),
        )


def visit_report(con: sqlite3.Connection, days: int = 30) -> str:
    """Per day: distinct visitors, page loads, visitors who clicked something, clicks."""
    from zoneinfo import ZoneInfo

    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date()
    since = (today - dt.timedelta(days=days - 1)).isoformat()
    rows = con.execute(
        "SELECT date, COUNT(*), SUM(pages), SUM(actions > 0), SUM(actions) FROM visits "
        "WHERE date >= ? GROUP BY date ORDER BY date",
        (since,),
    ).fetchall()
    if not rows:
        return f"no visits logged in the last {days} days"
    out = ["date        visitors  page loads  clicked  clicks"]
    out += [f"{d}  {v:8d}  {p:10d}  {a:7d}  {c:6d}" for d, v, p, a, c in rows]
    n = con.execute("SELECT COUNT(DISTINCT visitor) FROM visits WHERE date >= ?", (since,)).fetchone()[0]
    out.append(f"{n} distinct visitors over these {len(rows)} day(s)")
    return "\n".join(out)


def explain_spent(con: sqlite3.Connection, since: str = "") -> tuple[int, float]:
    """(number of explanation calls, USD) since a date or time ("" = ever)."""
    n, usd = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM explain_costs WHERE at >= ?", (since,)
    ).fetchone()
    return n, usd
