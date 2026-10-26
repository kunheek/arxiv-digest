"""BibTeX for arXiv papers, built from the local DB in Google Scholar's form
(@article, journal = "arXiv preprint arXiv:<id>"), which every bibliography style prints.
No network and no LLM: title, authors and id are copied from arXiv's own metadata.
The site builds the same entry in app.js."""
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


def _protect(word: str) -> str:
    """Brace capitals that styles must not lowercase: RayCo4D, NeRF, 4D, SE(3)."""
    if re.search(r"[A-Z]", word[1:]) or (re.search(r"\d", word) and re.search(r"[A-Za-z]", word)):
        return "{" + word + "}"
    return word


def title(t: str) -> str:
    t = re.sub(r"(?<!\\)([&%#])", r"\\\1", " ".join(t.split()))
    return " ".join(
        w if re.search(r"[$\\{}]", w) else "".join(_protect(x) for x in re.split(r"([-/])", w))
        for w in t.split(" ")
    )


def entry(p: dict) -> str:
    # "First Last" as arXiv gives it: BibTeX parses that itself, so no guessing which part is the surname
    authors = " and ".join(clean_name(a) for a in p["authors"])
    return (
        f"@article{{{key(p)},\n"
        f"  title={{{title(p['title'])}}},\n"
        f"  author={{{authors}}},\n"
        f"  journal={{arXiv preprint arXiv:{p['id']}}},\n"
        f"  year={{{year(p['id'], p.get('date', ''))}}}\n}}\n"
    )
