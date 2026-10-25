"""BibTeX for arXiv papers, built from the local DB (same @misc form arxiv.org/bibtex/<id> gives).
No network: works offline and for every paper. The site builds the same entry in app.js."""
from __future__ import annotations

import re
import unicodedata

from .site import clean_name

_STOP = {"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "towards", "toward", "via", "is"}


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def year(pid: str, date: str = "") -> str:
    """Submission year from a new-style id (2610.01234 → 2026), else from the digest date."""
    m = re.match(r"(\d{2})\d{2}\.", pid)
    return "20" + m[1] if m else (date or "")[:4]


def key(p: dict) -> str:
    authors = [clean_name(a) for a in p["authors"]]
    last = re.sub(r"[^a-z]", "", _ascii(authors[0].split()[-1]).lower()) if authors else "anon"
    word = next((w for w in re.findall(r"[a-z0-9]+", _ascii(p["title"]).lower()) if w not in _STOP), "")
    return f"{last}{year(p['id'], p.get('date', ''))}{word}"


def entry(p: dict) -> str:
    authors = " and ".join(clean_name(a) for a in p["authors"])
    title = re.sub(r"(?<!\\)&", r"\\&", " ".join(p["title"].split()))
    cats = p.get("categories") or []
    return (
        f"@misc{{{key(p)},\n"
        f"      title={{{title}}},\n"
        f"      author={{{authors}}},\n"
        f"      year={{{year(p['id'], p.get('date', ''))}}},\n"
        f"      eprint={{{p['id']}}},\n"
        f"      archivePrefix={{arXiv}},\n"
        + (f"      primaryClass={{{cats[0]}}},\n" if cats else "")
        + f"      url={{https://arxiv.org/abs/{p['id']}}},\n}}\n"
    )
