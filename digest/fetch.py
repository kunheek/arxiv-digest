"""Fetch today's announcements from arXiv RSS feeds."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

RSS_URL = "https://rss.arxiv.org/rss/{cat}"
NS = {
    "arxiv": "http://arxiv.org/schemas/atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}
UA = {"User-Agent": "arxiv-daily-digest/1.0 (personal research feed)"}


@dataclass
class Paper:
    id: str  # e.g. 2610.01234 (no version)
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    announce_type: str
    link: str
    # filled in later
    interest: float = 0.0  # relevance to you, 0-10
    quality: float = 5.0  # estimated rigor / significance, 0-10
    quality_note: str = ""
    impact: float = 0.0  # field-wide importance regardless of your interests, 0-10
    reason: str = ""
    tags: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    intro: str = ""
    author_hits: list[str] = field(default_factory=list)
    inst_hits: list[str] = field(default_factory=list)
    final: float = 0.0
    tldr: str = ""
    why: str = ""
    code_url: str = ""


def _text(el, path, ns=NS):
    found = el.find(path, ns)
    return (found.text or "").strip() if found is not None else ""


def parse_rss(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers = []
    for item in root.iter("item"):
        desc = _text(item, "description")
        m = re.search(r"arXiv:(\S+?)(v\d+)?\s", desc + " ")
        pid = m.group(1) if m else _text(item, "link").rsplit("/", 1)[-1]
        abstract = desc.split("Abstract:", 1)[-1].strip()
        authors = [a.strip() for a in _text(item, "dc:creator").split(",") if a.strip()]
        papers.append(
            Paper(
                id=pid,
                title=" ".join(_text(item, "title").split()),
                abstract=" ".join(abstract.split()),
                authors=authors,
                categories=[c.text.strip() for c in item.findall("category") if c.text],
                announce_type=_text(item, "arxiv:announce_type") or "new",
                link=f"https://arxiv.org/abs/{pid}",
            )
        )
    return papers


def fetch_today(categories: list[str], announce_types: list[str]) -> list[Paper]:
    """Fetch each category separately (combined feeds cap at 2000 items) and de-duplicate."""
    by_id: dict[str, Paper] = {}
    for i, cat in enumerate(categories):
        if i:
            time.sleep(3)  # be polite to arXiv
        r = requests.get(RSS_URL.format(cat=cat), headers=UA, timeout=60)
        r.raise_for_status()
        for p in parse_rss(r.text):
            if p.announce_type not in announce_types:
                continue
            by_id.setdefault(p.id, p)
        print(f"  {cat}: running total {len(by_id)} unique papers")
    return list(by_id.values())
