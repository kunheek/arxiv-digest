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
# Newest day is inserted at the top (row 2), best paper first, so the sheet
# "stacks" day by day. The header row has filter dropdowns.
COL = {name: i for i, name in enumerate(SHEET_HEADER)}
WIDTHS = {"date": 90, "arxiv_id": 95, "title": 380, "final_score": 70, "interest": 65,
          "watch_hits": 160, "tldr": 420, "why": 300, "reason": 220, "tags": 160,
          "authors": 220, "affiliations": 220, "categories": 110, "code": 160,
          "link": 160, "read?": 55, "my rating (1-5)": 80}


def _sheet(cfg):
    import gspread
    creds = json.loads(os.environ["GOOGLE_CREDS"])
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_key(os.environ["SHEET_ID"])
    name = cfg["delivery"].get("sheet_worksheet", "papers")
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(name, rows=1000, cols=len(SHEET_HEADER))


def _grid(ws, col, r0=1, r1=None, c1=None):
    g = {"sheetId": ws.id, "startRowIndex": r0, "startColumnIndex": col,
         "endColumnIndex": (c1 if c1 is not None else col + 1)}
    if r1 is not None:
        g["endRowIndex"] = r1
    return g


def _has_filter(ws) -> bool:
    meta = ws.spreadsheet.fetch_sheet_metadata(
        {"fields": "sheets(properties.sheetId,basicFilter)"})
    return any(sh["properties"]["sheetId"] == ws.id and "basicFilter" in sh
               for sh in meta.get("sheets", []))


def _setup_once(ws) -> None:
    """Header, filter, widths, wrapping, score colours. Runs when no filter exists yet."""
    n = len(SHEET_HEADER)
    if ws.row_values(1) != SHEET_HEADER:
        ws.update(values=[SHEET_HEADER], range_name="A1")
    green = lambda r, g, b: {"red": r, "green": g, "blue": b}
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": ws.id,
            "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 3}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        {"repeatCell": {"range": _grid(ws, 0, 0, 1, n), "cell": {"userEnteredFormat": {
            "textFormat": {"bold": True}, "backgroundColor": green(0.85, 0.89, 0.95),
            "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(textFormat,backgroundColor,wrapStrategy,verticalAlignment)"}},
        # filter dropdowns on every header column (covers rows added later)
        {"setBasicFilter": {"filter": {"range": _grid(ws, 0, 0, None, n)}}},
        # long text columns: clip instead of making rows huge
        {"repeatCell": {"range": _grid(ws, 0, 1, None, n), "cell": {"userEnteredFormat": {
            "wrapStrategy": "CLIP", "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}},
        # score colours: >= 9 dark green, >= 7 light green
        {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [_grid(ws, COL["final_score"])],
            "booleanRule": {"condition": {"type": "NUMBER_GREATER_THAN_EQ",
                                          "values": [{"userEnteredValue": "9"}]},
                            "format": {"backgroundColor": green(0.58, 0.83, 0.6),
                                       "textFormat": {"bold": True}}}}}},
        {"addConditionalFormatRule": {"index": 1, "rule": {
            "ranges": [_grid(ws, COL["final_score"])],
            "booleanRule": {"condition": {"type": "NUMBER_GREATER_THAN_EQ",
                                          "values": [{"userEnteredValue": "7"}]},
                            "format": {"backgroundColor": green(0.85, 0.94, 0.85)}}}}},
    ]
    for name, px in WIDTHS.items():
        reqs.append({"updateDimensionProperties": {"range": {
            "sheetId": ws.id, "dimension": "COLUMNS",
            "startIndex": COL[name], "endIndex": COL[name] + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    ws.spreadsheet.batch_update({"requests": reqs})


def _row(p: Paper, date: str) -> list:
    title = p.title.replace('"', '""')
    return [date, p.id, f'=HYPERLINK("{p.link}", "{title}")', p.final, p.interest,
            "; ".join(p.author_hits + p.inst_hits), p.tldr, p.why, p.reason,
            ", ".join(p.tags), ", ".join(p.authors[:20]), " | ".join(p.affiliations)[:2000],
            ", ".join(p.categories), p.code_url, p.link, False, ""]


def to_sheets(papers: list[Paper], date: str, cfg: dict) -> None:
    if not papers:
        return
    ws = _sheet(cfg)
    if not _has_filter(ws):
        _setup_once(ws)
    rows = [_row(p, date) for p in papers]          # already ranked best-first
    ws.insert_rows(rows, row=2, value_input_option="USER_ENTERED")
    r0, r1 = 1, 1 + len(rows)                       # 0-based rows of the new block
    ws.spreadsheet.batch_update({"requests": [
        # "read?" → checkbox
        {"setDataValidation": {"range": _grid(ws, COL["read?"], r0, r1),
                               "rule": {"condition": {"type": "BOOLEAN"}}}},
        # "my rating" → 1-5 dropdown
        {"setDataValidation": {"range": _grid(ws, COL["my rating (1-5)"], r0, r1),
                               "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                                   {"userEnteredValue": str(i)} for i in range(1, 6)]},
                                        "showCustomUi": True, "strict": True}}},
        # new rows must not inherit the header's bold/blue style
        {"repeatCell": {"range": _grid(ws, 0, r0, r1, len(SHEET_HEADER)),
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": False},
                                 "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                                 "wrapStrategy": "CLIP", "verticalAlignment": "TOP"}},
                        "fields": "userEnteredFormat(textFormat.bold,backgroundColor,"
                                  "wrapStrategy,verticalAlignment)"}},
        # thin line above the new day so days are easy to tell apart
        {"updateBorders": {"range": _grid(ws, 0, r1 - 1, r1, len(SHEET_HEADER)),
                           "bottom": {"style": "SOLID_MEDIUM",
                                      "color": {"red": 0.4, "green": 0.4, "blue": 0.4}}}},
    ]})


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
    for r in records:                               # newest first (top of sheet)
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
