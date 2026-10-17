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


def all_papers(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM papers ORDER BY date DESC, final DESC, interest DESC"
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
    """Load a ratings.json exported from the website: {"ratings": {"2610.01234": 5, ...}}.
    A rating of 0 removes the rating."""
    with open(path) as f:
        data = json.load(f)
    ratings = data.get("ratings", data)
    now = dt.date.today().isoformat()
    n = 0
    with con:
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
        "SELECT p.title FROM ratings r JOIN papers p ON p.id = r.id "
        "WHERE r.rating >= ? ORDER BY r.rated_at DESC, p.date DESC LIMIT ?",
        (min_rating, limit),
    )
    return [r["title"] for r in rows]
