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
def notify(webhook: str, text: str) -> None:
    for _ in range(3):
        r = requests.post(webhook, json={"content": text}, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        r.raise_for_status()
        return


def to_discord(date: str, stats: dict, webhook: str, url: str = "") -> None:
    """One short "digest updated" message; the papers themselves live on the website."""
    lines = [f"📚 **arXiv digest updated** — {date}",
             f"Scanned **{stats['total']}** papers · **{stats.get('on_site', 0)}** on the site"
             f" · **{stats.get('top', 0)}** top picks"]
    if url:
        lines.append(f"<{url}>")
    notify(webhook, "\n".join(lines))


def to_discord_empty(date: str, webhook: str, retry_at: str = "") -> None:
    """arXiv's feed had no papers at all (US holiday, or the feed is late)."""
    text = (f"📭 **arXiv digest** — {date}: no new papers in the arXiv feed "
            f"(likely a US holiday or a late feed).")
    if retry_at:
        text += f" Will check again at {retry_at}."
    notify(webhook, text)


# ── Markdown (always written; handy for dry runs) ──
def to_markdown(papers: list[Paper], date: str, stats: dict, path: str) -> None:
    lines = [
        f"# arXiv digest — {date}",
        f"Scanned {stats['total']} papers · {stats['calls']} Claude calls · cost ≈ ${stats['cost']:.3f}\n",
    ]
    for i, p in enumerate(papers, 1):
        lines += [
            f"## {i}. [{p.title}]({p.link})",
            f"*score {p.final:g} (interest {p.interest:g}) · {', '.join(p.tags)}*  ",
            f"{', '.join(p.authors[:8])}{' et al.' if len(p.authors) > 8 else ''}  ",
            f"{_badges(p)}\n" if _badges(p) else "",
            f"**TL;DR** {p.tldr}  ",
            f"**Why you** {p.why}\n",
        ]
    with open(path, "w") as f:
        f.write("\n".join(lines))
