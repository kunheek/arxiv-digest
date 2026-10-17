"""Delivery: Discord webhook and a local markdown file."""
from __future__ import annotations

import time

import requests

from .fetch import Paper


def _badges(p: Paper) -> str:
    b = []
    if p.author_hits:
        b.append("👤 " + ", ".join(p.author_hits))
    if p.inst_hits:
        b.append("🏛 " + ", ".join(p.inst_hits[:3]))
    if p.code_url:
        b.append(f"[💻 code]({p.code_url})")
    return " · ".join(b)


# ── Discord ────────────────────────────────────────────────────
def to_discord(papers: list[Paper], date: str, stats: dict, webhook: str) -> None:
    def post(payload):
        for _ in range(3):
            r = requests.post(webhook, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 2)))
                continue
            r.raise_for_status()
            return

    post({"content": f"## 📚 arXiv digest — {date}\n"
                     f"Scanned **{stats['total']}** papers · showing top **{len(papers)}** · "
                     f"cost ≈ ${stats['cost']:.3f}"})
    embeds = []
    for i, p in enumerate(papers, 1):
        desc = f"**TL;DR** {p.tldr}\n**Why you** {p.why}"
        if _badges(p):
            desc += f"\n{_badges(p)}"
        embeds.append({
            "title": f"{i}. {p.title}"[:256],
            "url": p.link,
            "description": desc[:4000],
            "color": 0x2ECC71 if p.final >= 9 else 0x3498DB if p.final >= 7 else 0x95A5A6,
            "footer": {"text": f"score {p.final:g} · {', '.join(p.tags)} · "
                               f"{', '.join(p.authors[:3])}{' et al.' if len(p.authors) > 3 else ''}"[:2048]},
        })
    # Discord: max 10 embeds and ~6000 chars per message
    batch, size = [], 0
    for e in embeds:
        n = len(e["title"]) + len(e["description"]) + len(e["footer"]["text"])
        if batch and (len(batch) == 10 or size + n > 5500):
            post({"embeds": batch})
            batch, size = [], 0
        batch.append(e)
        size += n
    if batch:
        post({"embeds": batch})


# ── Markdown (always written; handy for dry runs) ──
def to_markdown(papers: list[Paper], date: str, stats: dict, path: str) -> None:
    lines = [f"# arXiv digest — {date}",
             f"Scanned {stats['total']} papers · {stats['calls']} Claude calls · cost ≈ ${stats['cost']:.3f}\n"]
    for i, p in enumerate(papers, 1):
        lines += [f"## {i}. [{p.title}]({p.link})",
                  f"*score {p.final:g} (interest {p.interest:g}) · {', '.join(p.tags)}*  ",
                  f"{', '.join(p.authors[:8])}{' et al.' if len(p.authors) > 8 else ''}  ",
                  f"{_badges(p)}\n" if _badges(p) else "",
                  f"**TL;DR** {p.tldr}  ", f"**Why you** {p.why}\n"]
    with open(path, "w") as f:
        f.write("\n".join(lines))
