"""Build the static website from the SQLite archive.

Layout (scales to tens of thousands of papers):
  index.html, app.js, style.css  – tiny single-page app, hash routing
  data/index.json                – one compact row per paper (search + day lists)
  data/d/YYYY-MM-DD.json         – full details, one shard per day, loaded on demand
  data/x/<id>.json               – a paper's explanation (digest/explain.py), if it has one
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata

from . import db

ASSETS = os.path.join(os.path.dirname(__file__), "site_assets")


_ACCENTS = {
    "'": "\u0301",
    "`": "\u0300",
    '"': "\u0308",
    "^": "\u0302",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
}


def clean_name(name: str) -> str:
    """arXiv metadata keeps LaTeX accents, e.g. G\\"uler, R{\\i}za → Güler, Rıza."""
    name = re.sub(
        r"\\([\'`\"^~=.])\{?\\?([A-Za-z])\}?",
        lambda m: unicodedata.normalize("NFC", m[2] + _ACCENTS[m[1]]),
        name,
    )
    name = name.replace("{\\i}", "ı").replace("\\i", "ı")
    return re.sub(r"[{}]", "", name)


def _dump(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def index_row(p: dict) -> list:
    au = [clean_name(a) for a in p["authors"]]
    short = au if len(au) <= 8 else au[:6] + au[-2:]  # index keeps first 6 + last 2
    return [
        p["id"],
        p["date"],
        p["title"],
        p["final"],
        p["tags"],
        short,
        1 if (p["author_hits"] or p["inst_hits"]) else 0,
        1 if p["code_url"] else 0,
        p["categories"][:4],
        len(au),
        p["quality"],
        p["impact"] or 0,
    ]


def index_payload(papers: list[dict], days: dict) -> dict:
    return {
        "papers": [index_row(p) for p in papers],
        "days": {d: {"total": v["total"]} for d, v in days.items()},
    }


def shard_entry(p: dict) -> dict:
    return {
        "tldr": p["tldr"],
        "why": p["why"],
        "reason": p["reason"],
        "qn": p["quality_note"],
        "abs": p["abstract"],
        "au": [clean_name(a) for a in p["authors"]],
        "cat": p["categories"],
        "code": p["code_url"],
        "ah": p["author_hits"],
        "ih": p["inst_hits"],
    }


def shards_for(papers: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in papers:
        out.setdefault(p["date"], {})[p["id"]] = shard_entry(p)
    return out


def page_html(title: str, local: bool = False) -> str:
    with open(os.path.join(ASSETS, "index.html")) as f:
        return f.read().replace("{{TITLE}}", title + (" (local)" if local else ""))


def build(
    con,
    out: str = "site",
    title: str = "arXiv digest",
    min_interest: float = 0,
    min_impact: float = 11,
    max_papers: int = 0,
) -> int:
    """Papers shown: relevance >= min_interest OR impact >= min_impact (foundational work),
    newest `max_papers` of them (0 = no cap)."""
    papers = [
        p
        for p in db.all_papers(con)
        if p["interest"] >= min_interest or (p["impact"] or 0) >= min_impact
    ]
    rated, saved, liked = db.ratings(con), set(db.bookmarks(con)), set(db.likes(con))
    if max_papers > 0:  # all_papers() is newest first, so this keeps the most recent ones
        # ...plus everything you rated or bookmarked, so the public view mirrors your picks
        papers = [
            p for i, p in enumerate(papers) if i < max_papers or p["id"] in rated or p["id"] in saved or p["id"] in liked
        ]
    days = {r["date"]: dict(r) for r in con.execute("SELECT * FROM days")}
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for name in os.listdir(ASSETS):
        shutil.copy(os.path.join(ASSETS, name), out)
    with open(os.path.join(out, "index.html"), "w") as f:
        f.write(page_html(title))
    open(os.path.join(out, ".nojekyll"), "w").close()

    for day, shard in shards_for(papers).items():
        _dump(os.path.join(out, "data", "d", f"{day}.json"), shard)
    _dump(os.path.join(out, "data", "index.json"), index_payload(papers, days))
    # Read-only mirror of the preferences you set on the local site (ids on this site only).
    ids = {p["id"] for p in papers}
    explained = sorted(set(db.explained(con)) & ids)
    for pid in explained:  # read-only here: requesting a new one needs the local server
        _dump(os.path.join(out, "data", "x", f"{pid}.json"), db.get_explanation(con, pid))
    _dump(
        os.path.join(out, "data", "prefs.json"),
        {
            "ratings": {i: r for i, r in rated.items() if i in ids},
            "bookmarks": sorted(saved & ids),
            "likes": sorted(liked & ids),
            "explained": explained,
        },
    )
    return len(papers)
