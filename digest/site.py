"""Build the static website from the SQLite archive.

Layout (scales to tens of thousands of papers):
  index.html, app.js, style.css  – tiny single-page app, hash routing
  data/index.json                – one compact row per paper (search + day lists)
  data/YYYY-MM.json              – full details, one shard per month, loaded on demand
"""
from __future__ import annotations

import json
import os
import shutil

from . import db

ASSETS = os.path.join(os.path.dirname(__file__), "site_assets")


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
        w = 1 if (p["author_hits"] or p["inst_hits"]) else 0
        index.append([p["id"], p["date"], p["title"], p["final"], p["interest"],
                      p["tags"], p["authors"][:6], w])
        shards.setdefault(p["date"][:7], {})[p["id"]] = {
            "tldr": p["tldr"], "why": p["why"], "reason": p["reason"],
            "abs": p["abstract"], "au": p["authors"], "af": p["affiliations"],
            "cat": p["categories"], "link": p["link"], "code": p["code_url"],
            "ah": p["author_hits"], "ih": p["inst_hits"],
        }
    _dump(os.path.join(out, "data", "index.json"),
          {"papers": index,
           "days": {d: {"total": v["total"]} for d, v in days.items()}})
    for month, shard in shards.items():
        _dump(os.path.join(out, "data", f"{month}.json"), shard)
    return len(papers)
