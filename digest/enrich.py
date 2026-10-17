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


_NOISE = re.compile(r"equal contribution|corresponding|project lead|work done|^\W*$|@|^email|"
                   r"^\W*(?:\d+|[*†‡§¶])\W*$", re.I)


def _split(text: str) -> list[str]:
    """Affiliations that share one element are separated by runs of whitespace/newlines."""
    return [" ".join(t.split()) for t in re.split(r"\s{2,}|\n", text)]


def parse_affiliations(soup: BeautifulSoup, authors: list[str] = ()) -> list[str]:
    """Institution names from an arXiv HTML page, or [] if they can't be told apart
    from author names (better nothing than a pasted author block)."""
    block = soup.select_one(".ltx_authors")
    if not block:
        return []
    raw = []
    contacts = block.select(".ltx_contact.ltx_role_affiliation")
    if contacts:
        for el in contacts:
            for lab in el.select(".ltx_contact_name"):
                lab.decompose()
            raw += _split(el.get_text())
    else:
        # affiliations typed as plain text under the names: drop names (bold), footnote
        # marks and notes, and keep what's left
        block = BeautifulSoup(str(block), "html.parser")
        for el in block.select(".ltx_font_bold, sup, .ltx_note, .ltx_tag"):
            el.decompose()
        raw = _split(block.get_text())
    names = {_norm(a) for a in authors if _norm(a)}
    out = []
    for t in raw:
        t = t.strip(" ,;*†‡§¶0123456789")
        if len(t) < 3 or len(t) > 150 or _NOISE.search(t) or _norm(t) in names:
            continue
        if any(n and n in _norm(t) for n in names):      # contains an author name
            continue
        out.append(t)
    return list(dict.fromkeys(out))[:20]


def fetch_html(p: Paper, max_intro_chars: int = 6000) -> None:
    """Pull affiliations and the introduction. Not every paper has an HTML version."""
    try:
        r = requests.get(HTML_URL.format(id=p.id), headers=UA, timeout=30)
        if r.status_code != 200:
            return
    except requests.RequestException:
        return
    soup = BeautifulSoup(r.text, "html.parser")

    p.affiliations = parse_affiliations(soup, p.authors)

    first = soup.select_one("section.ltx_section")
    if first:
        p.intro = " ".join(first.get_text(" ").split())[:max_intro_chars]

    if not p.code_url:
        m = CODE_RE.search(p.abstract) or CODE_RE.search(r.text[:200_000])
        if m:
            p.code_url = m.group(0).rstrip(".)")


def final_score(p: Paper, cfg: dict) -> float:
    """Relevance, scaled by quality, plus watchlist bonuses.

    quality only modulates relevance (quality_weight w: a 0-quality paper keeps 1-w of its
    relevance, a 10-quality paper keeps all of it), so a great but irrelevant paper never
    outranks a relevant one, and a relevant but weak paper sinks a few points."""
    r = cfg["ranking"]
    w = r.get("quality_weight", 0.4)
    base = p.interest * (1 - w + w * p.quality / 10)
    bonus = 0.0
    if p.interest >= r["min_interest_for_bonus"]:
        bonus += r["author_bonus"] if p.author_hits else 0
        bonus += r["institution_bonus"] if p.inst_hits else 0
    return round(base + bonus, 2)


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
        p.final = final_score(p, cfg)
    return shortlist
