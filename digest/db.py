"""Local SQLite archive of every scored paper (the source of truth for the website)."""
from __future__ import annotations

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
  interest REAL, final REAL, reason TEXT, tldr TEXT, why TEXT,
  author_hits TEXT, inst_hits TEXT
);
CREATE INDEX IF NOT EXISTS papers_date ON papers(date);
CREATE TABLE IF NOT EXISTS days (
  date TEXT PRIMARY KEY, total INTEGER, cost REAL, calls INTEGER
);
"""
JSON_COLS = ("authors", "affiliations", "categories", "tags", "author_hits", "inst_hits")


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert(con: sqlite3.Connection, papers: list[Paper], date: str,
           stats: dict | None = None) -> None:
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
                f"VALUES ({','.join('?' * len(cols))})", [d[c] for c in cols])
        if stats:
            con.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?)",
                        (date, stats.get("total"), stats.get("cost"), stats.get("calls")))


def all_papers(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute("SELECT * FROM papers ORDER BY date DESC, final DESC, interest DESC")
    out = []
    for r in rows:
        d = dict(r)
        for c in JSON_COLS:
            d[c] = json.loads(d[c] or "[]")
        out.append(d)
    return out


def import_results(con: sqlite3.Connection, folder: str = "results") -> int:
    """Backfill from results/YYYY-MM-DD.json files."""
    n = 0
    if not os.path.isdir(folder):
        return 0
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(folder, name)) as f:
            papers = [Paper(**d) for d in json.load(f)]
        upsert(con, papers, name[:-5])
        n += len(papers)
    return n
