"""Stage 2: watchlist matching + affiliations/intro from the arXiv HTML page."""
from __future__ import annotations

import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

from .fetch import UA, Paper

HTML_URL = "https://arxiv.org/html/{id}"
CODE_RE = re.compile(r"https?://(?:github\.com|huggingface\.co|gitlab\.com)/[\w\-./]+", re.I)


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def match_authors(p: Paper, watch: list[str]) -> None:
    authors = {_norm(a) for a in p.authors}
    p.author_hits = [w for w in watch if _norm(w) in authors]


def match_institutions(p: Paper, watch: list[str]) -> None:
    hay = " " + " ".join(p.affiliations) + " "
    hits = []
    for entry in watch:
        aliases = [a.strip() for a in entry.split("|")]
        for a in aliases:
            # word-boundary match so "MIT" doesn't fire on "submitted"
            if re.search(rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])", hay,
                         flags=0 if a.isupper() else re.I):
                hits.append(aliases[0])
                break
    p.inst_hits = hits


def fetch_html(p: Paper, max_intro_chars: int = 6000) -> None:
    """Pull affiliations and the introduction. Not every paper has an HTML version."""
    try:
        r = requests.get(HTML_URL.format(id=p.id), headers=UA, timeout=30)
        if r.status_code != 200:
            return
    except requests.RequestException:
        return
    soup = BeautifulSoup(r.text, "html.parser")

    authors_block = soup.select_one(".ltx_authors")
    if authors_block:
        affs = [" ".join(el.get_text(" ").split())
                for el in authors_block.select(".ltx_contact, .ltx_role_affiliation, .ltx_affiliation")]
        if not affs:  # many templates just put affiliations as plain text in the block
            affs = [" ".join(authors_block.get_text(" ").split())]
        p.affiliations = list(dict.fromkeys(a for a in affs if a))[:20]

    first = soup.select_one("section.ltx_section")
    if first:
        p.intro = " ".join(first.get_text(" ").split())[:max_intro_chars]

    if not p.code_url:
        m = CODE_RE.search(p.abstract) or CODE_RE.search(r.text[:200_000])
        if m:
            p.code_url = m.group(0).rstrip(".)")


def enrich(papers: list[Paper], cfg: dict) -> list[Paper]:
    r = cfg["ranking"]
    for p in papers:
        match_authors(p, cfg.get("watch_authors", []))
        m = CODE_RE.search(p.abstract)
        if m:
            p.code_url = m.group(0).rstrip(".)")

    # HTML only for the shortlist: top interest + any watched-author paper worth a look
    by_interest = sorted(papers, key=lambda p: p.interest, reverse=True)
    shortlist = by_interest[: cfg["limits"]["enrich_top_n"]]
    shortlist += [p for p in papers if p.author_hits and p not in shortlist
                  and p.interest >= r["min_interest_for_bonus"]]
    print(f"  fetching HTML for {len(shortlist)} shortlisted papers")
    for i, p in enumerate(shortlist):
        if i:
            time.sleep(1)  # gentle on arxiv.org
        fetch_html(p)
        match_institutions(p, cfg.get("watch_institutions", []))

    for p in papers:
        bonus = 0.0
        if p.interest >= r["min_interest_for_bonus"]:
            bonus += r["author_bonus"] if p.author_hits else 0
            bonus += r["institution_bonus"] if p.inst_hits else 0
        p.final = round(p.interest + bonus, 2)
    return shortlist
