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


CATEGORY_NAMES = {
    "cs.AI": "Artificial Intelligence",
    "cs.CG": "Computational Geometry",
    "cs.CL": "Computation and Language",
    "cs.CV": "Computer Vision and Pattern Recognition",
    "cs.GR": "Graphics",
    "cs.HC": "Human-Computer Interaction",
    "cs.LG": "Machine Learning",
    "cs.MM": "Multimedia",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.RO": "Robotics",
    "eess.IV": "Image and Video Processing",
    "stat.ML": "Machine Learning (statistics)",
}


def _model_name(model: str) -> str:
    """claude-haiku-5-5 → Claude Haiku 5.5"""
    words = model.split("-")
    nums = [w for w in words if w.isdigit()]
    names = [w.capitalize() for w in words if not w.isdigit()]
    return " ".join(names + [".".join(nums)] if nums else names)


def about_html(cfg: dict) -> str:
    """The "About this digest" dialog (delivery.about): which feeds, and how papers get ranked,
    written from the config so it stays true when the config changes."""
    from html import escape as e

    lim, rk, md = cfg.get("limits", {}), cfg.get("ranking", {}), cfg.get("models", {})
    fb, xp = cfg.get("feedback", {}), cfg.get("explain", {})
    cats = "".join(
        f"<li><a href=\"https://arxiv.org/list/{e(c)}/new\" target=\"_blank\" rel=\"noopener\">"
        f"<code>{e(c)}</code></a> {e(CATEGORY_NAMES.get(c, ''))}</li>"
        for c in cfg.get("categories", [])
    )
    kinds = set(cfg.get("announce_types", ["new", "cross"]))
    which = "New submissions" + (" and cross-lists from other categories" if "cross" in kinds else "")
    steps = [
        "<b>Fetch.</b> Every weekday, after arXiv's daily announcement, the day's papers in "
        "these categories come in from arXiv's feeds.",
        f"<b>Score.</b> {e(_model_name(md.get('scorer', 'claude-haiku')))} reads every abstract and "
        "scores it 0–10 three ways: relevance to the lab's interest profile (below), quality, and "
        "field-wide impact.",
        f"<b>Check.</b> The top {lim.get('enrich_top_n', 30)} get their authors' affiliations "
        "checked; papers from watched authors or institutions (⭐) get a bonus.",
        f"<b>Review.</b> {e(_model_name(md.get('summarizer', 'claude-sonnet')))} re-checks the "
        f"impact of the top {rk.get('impact_review_n', 20)} and writes TL;DRs for the top "
        f"{lim.get('summarize_top_k', 10)}.",
        "<b>Rank.</b> The score shown is relevance, lowered a little for weak papers, plus the "
        f"watchlist bonus. Papers with impact ≥ {rk.get('impact_min', 7)} (📌) stay near the top "
        "whatever their topic.",
    ]
    if fb.get("enabled"):
        steps.append(
            "<b>Learn.</b> Ratings, likes and bookmarks are shared by everyone here. Papers rated "
            f"{fb.get('min_rating', 4)}★ or more, liked or bookmarked (up to "
            f"{fb.get('max_examples', 30)}) are shown to the scorer as examples of the lab's taste "
            "from the next day on, and <i>for you ♥</i> sorts by similarity to them."
        )
    if xp.get("enabled", True):
        budget = xp.get("daily_budget_usd", 5)
        steps.append(
            f"<b>Explain.</b> Expand a paper and press <i>Explain this paper</i>: "
            f"{e(_model_name(xp.get('model', 'claude-opus')))} reads the whole PDF and writes up its "
            "key points (about a minute, ≈ $0.20" + (f"; ${budget} a day for everyone" if budget else "")
            + "). Explanations are saved, so each paper is explained once."
        )
    profile = e(cfg.get("interest_profile", "").strip())
    return (
        '<dialog id="aboutbox">\n  <h2>About this digest</h2>\n'
        f"  <p>{which} in these arXiv categories, ranked for the lab:</p>\n"
        f'  <ul class="cats">{cats}</ul>\n'
        "  <h2>How it works</h2>\n  <ol>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>\n"
        f"  <details><summary>Interest profile used for scoring</summary><pre>{profile}</pre></details>\n"
        '  <p class="qn">Press <kbd>?</kbd> for search syntax and shortcuts, <kbd>i</kbd> to see this again.</p>\n'
        '  <form method="dialog"><button class="btn">Got it</button></form>\n</dialog>'
    )


def page_html(title: str, local: bool = False, about: str = "") -> str:
    """`about`: the about_html() dialog, or "" for none (the ⓘ button stays hidden)."""
    with open(os.path.join(ASSETS, "index.html")) as f:
        page = f.read().replace("{{TITLE}}", title + (" (local)" if local else ""))
    return page.replace("{{ABOUT}}", about)


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
