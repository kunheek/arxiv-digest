"""Build the static website from the SQLite archive.

Layout (scales to tens of thousands of papers):
  index.html, app.js, style.css  – tiny single-page app, hash routing
  data/index.json                – one compact row per paper (search + day lists)
  data/d/YYYY-MM-DD.json         – full details, one shard per day, loaded on demand
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata

from . import db

ASSETS = os.path.join(os.path.dirname(__file__), "site_assets")


_ACCENTS = {"'": "\u0301", "`": "\u0300", '"': "\u0308", "^": "\u0302", "~": "\u0303", "=": "\u0304", ".": "\u0307"}


def clean_name(name: str) -> str:
    """arXiv metadata keeps LaTeX accents, e.g. G\\"uler, R{\\i}za → Güler, Rıza."""
    name = re.sub(r"\\([\'`\"^~=.])\{?\\?([A-Za-z])\}?",
                  lambda m: unicodedata.normalize("NFC", m[2] + _ACCENTS[m[1]]), name)
    name = name.replace("{\\i}", "ı").replace("\\i", "ı")
    return re.sub(r"[{}]", "", name)


def _dump(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def build(con, out: str = "site", title: str = "arXiv digest", min_interest: float = 0) -> int:
    papers = [p for p in db.all_papers(con) if p["interest"] >= min_interest]
    days = {r["date"]: dict(r) for r in con.execute("SELECT * FROM days")}
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for name in os.listdir(ASSETS):
        shutil.copy(os.path.join(ASSETS, name), out)
    with open(os.path.join(out, "index.html")) as f:
        html = f.read().replace("{{TITLE}}", title)
    with open(os.path.join(out, "index.html"), "w") as f:
        f.write(html)
    open(os.path.join(out, ".nojekyll"), "w").close()

    index, shards = [], {}
    for p in papers:
        au = [clean_name(a) for a in p["authors"]]
        short = au if len(au) <= 8 else au[:6] + au[-2:]       # index keeps first 6 + last 2
        index.append([p["id"], p["date"], p["title"], p["final"], p["tags"], short,
                      1 if (p["author_hits"] or p["inst_hits"]) else 0,
                      1 if p["code_url"] else 0, p["categories"][:4], len(au), p["quality"]])
        shard = shards.setdefault(p["date"], {})
        shard[p["id"]] = {
            "tldr": p["tldr"], "why": p["why"], "reason": p["reason"], "qn": p["quality_note"],
            "abs": p["abstract"], "au": au, "cat": p["categories"], "code": p["code_url"],
            "ah": p["author_hits"], "ih": p["inst_hits"],
        }
    _dump(os.path.join(out, "data", "index.json"),
          {"papers": index,
           "days": {d: {"total": v["total"]} for d, v in days.items()}})
    for day, shard in shards.items():
        _dump(os.path.join(out, "data", "d", f"{day}.json"), shard)
    return len(papers)
