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
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
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


def all_papers(con: sqlite3.Connection, date: str | None = None) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM papers"
        + (" WHERE date = ?" if date else "")
        + " ORDER BY date DESC, final DESC, interest DESC",
        (date,) if date else (),
    )
    out = []
    for r in rows:
        d = dict(r)
        for c in JSON_COLS:
            d[c] = json.loads(d[c] or "[]")
        out.append(d)
    return out


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
    """Titles of papers you rated highly (newest first) → extra positive examples for scoring."""
    rows = con.execute(
        "SELECT p.title FROM papers p JOIN ("
        "  SELECT id, rated_at AS at FROM ratings WHERE rating >= ?"
        "  UNION SELECT id, liked_at FROM likes"
        ") x ON x.id = p.id ORDER BY x.at DESC, p.date DESC LIMIT ?",
        (min_rating, limit),
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
        con.execute(
            "DELETE FROM days WHERE date NOT IN (SELECT DISTINCT date FROM papers)"
        )
    return excess
