"""Delivery: Discord webhook, Google Sheets, and a local markdown file."""
from __future__ import annotations

import json
import os
import time

import requests

from .fetch import Paper

SHEET_HEADER = ["date", "arxiv_id", "title", "final_score", "interest", "watch_hits",
                "tldr", "why", "reason", "tags", "authors", "affiliations",
                "categories", "code", "link", "read?", "my rating (1-5)"]


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


# ── Google Sheets ──────────────────────────────────────────────
def _sheet(cfg):
    import gspread
    creds = json.loads(os.environ["GOOGLE_CREDS"])
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_key(os.environ["SHEET_ID"])
    name = cfg["delivery"].get("sheet_worksheet", "papers")
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(name, rows=1000, cols=len(SHEET_HEADER))
        ws.append_row(SHEET_HEADER)
        ws.freeze(rows=1)
        return ws


def to_sheets(papers: list[Paper], date: str, cfg: dict) -> None:
    ws = _sheet(cfg)
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADER)
    rows = [[date, p.id, p.title, p.final, p.interest,
             "; ".join(p.author_hits + p.inst_hits), p.tldr, p.why, p.reason,
             ", ".join(p.tags), ", ".join(p.authors[:20]), " | ".join(p.affiliations)[:2000],
             ", ".join(p.categories), p.code_url, p.link, "", ""] for p in papers]
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")


def liked_from_sheets(cfg: dict) -> list[str]:
    """Titles you rated highly → used as extra positive examples for scoring."""
    fb = cfg.get("feedback", {})
    if not fb.get("enabled") or not os.environ.get("GOOGLE_CREDS"):
        return []
    try:
        records = _sheet(cfg).get_all_records()
    except Exception as e:
        print(f"  (feedback skipped: {e})")
        return []
    liked = []
    for r in reversed(records):
        try:
            if int(r.get("my rating (1-5)") or 0) >= fb.get("min_rating", 4):
                liked.append(r["title"])
        except (ValueError, TypeError):
            continue
        if len(liked) >= fb.get("max_examples", 30):
            break
    return liked


# ── Markdown (always written; handy for dry runs and the Actions log) ──
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
