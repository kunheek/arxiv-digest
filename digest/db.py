"""Local SQLite archive of every scored paper (the source of truth for the website)."""

from __future__ import annotations

import datetime as dt
import json
import os
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
        bump_papers_rev(con)


def bump_papers_rev(con: sqlite3.Connection) -> None:
    """Mark papers/days as changed, so the local server rebuilds its index (likes and ratings
    don't touch it). Call inside the writing transaction."""
    con.execute(
        "INSERT INTO meta VALUES ('papers_rev', '1') "
        "ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1"
    )


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


def ratings(con: sqlite3.Connection) -> dict[str, int]:
    return {r["id"]: r["rating"] for r in con.execute("SELECT id, rating FROM ratings")}


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


def set_rating(con: sqlite3.Connection, pid: str, rating: int) -> None:
    with con:
        if rating <= 0:
            con.execute("DELETE FROM ratings WHERE id=?", (pid,))
        elif rating <= 5:
            con.execute(
                "INSERT OR REPLACE INTO ratings VALUES (?,?,?)",
                (pid, rating, dt.date.today().isoformat()),
            )


def set_bookmark(con: sqlite3.Connection, pid: str, on: bool) -> None:
    with con:
        if on:
            con.execute(
                "INSERT OR IGNORE INTO bookmarks VALUES (?,?)",
                (pid, dt.date.today().isoformat()),
            )
        else:
            con.execute("DELETE FROM bookmarks WHERE id=?", (pid,))


def set_like(con: sqlite3.Connection, pid: str, on: bool) -> None:
    with con:
        if on:
            con.execute(
                "INSERT OR IGNORE INTO likes VALUES (?,?)",
                (pid, dt.date.today().isoformat()),
            )
        else:
            con.execute("DELETE FROM likes WHERE id=?", (pid,))


def likes(con: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in con.execute("SELECT id FROM likes")]


def bookmarks(con: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in con.execute("SELECT id FROM bookmarks")]


def prune(con: sqlite3.Connection, max_papers: int) -> int:
    """Keep at most `max_papers` papers, deleting the oldest first (FIFO by date).
    Papers you rated, liked or bookmarked are never deleted and don't count toward the limit."""
    if not max_papers or max_papers <= 0:
        return 0
    special = "id IN (SELECT id FROM ratings UNION SELECT id FROM bookmarks UNION SELECT id FROM likes)"
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
