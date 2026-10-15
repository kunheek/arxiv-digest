"""Delivery: Discord webhook, Google Sheets, and a local markdown file."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from dataclasses import asdict

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


TAB_RE = re.compile(r"^\d{4}(-\d{2})?$")   # "2026" or "2026-10"


def tab_name(cfg: dict, date: str) -> str:
    """Which tab a given day goes to: monthly (default) → '2026-10', yearly → '2026',
    single → the fixed sheet_worksheet name."""
    mode = cfg.get("delivery", {}).get("sheet_tabs", "monthly")
    if mode == "yearly":
        return date[:4]
    if mode == "single":
        return cfg.get("delivery", {}).get("sheet_worksheet", "papers")
    return date[:7]


def _spreadsheet():
    import gspread
    creds = json.loads(os.environ["GOOGLE_CREDS"])
    return gspread.service_account_from_dict(creds).open_by_key(os.environ["SHEET_ID"])


def _sheet(cfg, date: str):
    """Open (or create, as the first tab) the tab for `date`."""
    import gspread
    sh = _spreadsheet()
    name = tab_name(cfg, date)
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        print(f"    creating new tab '{name}'")
        return sh.add_worksheet(name, rows=1000, cols=len(SHEET_HEADER), index=0)


def _grid(ws, col, r0=1, r1=None, c1=None):
    g = {"sheetId": ws.id, "startRowIndex": r0, "startColumnIndex": col,
         "endColumnIndex": (c1 if c1 is not None else col + 1)}
    if r1 is not None:
        g["endRowIndex"] = r1
    return g


def _meta(ws) -> dict:
    meta = ws.spreadsheet.fetch_sheet_metadata(
        {"fields": "sheets(properties.sheetId,basicFilter,conditionalFormats)"})
    return next((sh for sh in meta.get("sheets", [])
                 if sh["properties"]["sheetId"] == ws.id), {})


def _setup(ws, meta: dict) -> None:
    """Header, filter, widths, wrapping, score colours.

    Runs automatically when the sheet has no filter yet, or via --reformat.
    Score colours are only added if no rule exists on that column (no duplicates).
    """
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
    ]
    score_col = COL["final_score"]
    has_score_rule = any(r.get("startColumnIndex") == score_col
                         for rule in meta.get("conditionalFormats", [])
                         for r in rule.get("ranges", []))
    if not has_score_rule:  # score colours: >= 9 dark green, >= 7 light green
        for idx, (thr, colour, bold) in enumerate([("9", green(0.58, 0.83, 0.6), True),
                                                   ("7", green(0.85, 0.94, 0.85), False)]):
            reqs.append({"addConditionalFormatRule": {"index": idx, "rule": {
                "ranges": [_grid(ws, score_col)],
                "booleanRule": {"condition": {"type": "NUMBER_GREATER_THAN_EQ",
                                              "values": [{"userEnteredValue": thr}]},
                                "format": {"backgroundColor": colour,
                                           "textFormat": {"bold": bold}}}}}})
    for name, px in WIDTHS.items():
        reqs.append({"updateDimensionProperties": {"range": {
            "sheetId": ws.id, "dimension": "COLUMNS",
            "startIndex": COL[name], "endIndex": COL[name] + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    ws.spreadsheet.batch_update({"requests": reqs})


def _row(p: Paper, date: str, read=False, rating="") -> list:
    title = p.title.replace('"', '""')
    return [date, p.id, f'=HYPERLINK("{p.link}", "{title}")', p.final, p.interest,
            "; ".join(p.author_hits + p.inst_hits), p.tldr, p.why, p.reason,
            ", ".join(p.tags), ", ".join(p.authors[:20]), " | ".join(p.affiliations)[:2000],
            ", ".join(p.categories), p.code_url, p.link, read, rating]


def _rows_reqs(ws, r0: int, r1: int, day_ends: list[int]) -> list[dict]:
    """Checkbox + rating dropdown on rows [r0, r1) and a line under each day."""
    reqs = [
        {"setDataValidation": {"range": _grid(ws, COL["read?"], r0, r1),
                               "rule": {"condition": {"type": "BOOLEAN"}}}},
        {"setDataValidation": {"range": _grid(ws, COL["my rating (1-5)"], r0, r1),
                               "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                                   {"userEnteredValue": str(i)} for i in range(1, 6)]},
                                        "showCustomUi": True, "strict": True}}},
    ]
    # no font/colour override: rows keep the style of the row below them,
    # so your own fonts, colours and alternating colours carry over
    for r in day_ends:
        reqs.append({"updateBorders": {"range": _grid(ws, 0, r, r + 1, len(SHEET_HEADER)),
                     "bottom": {"style": "SOLID_MEDIUM",
                                "color": {"red": 0.4, "green": 0.4, "blue": 0.4}}}})
    return reqs


def _date_key(v) -> str:
    """Sheets stores dates as serial numbers; normalise to YYYY-MM-DD."""
    if isinstance(v, (int, float)) and v > 0:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(v))).isoformat()
    return str(v).strip()


def _data_rows(ws) -> list[list]:
    return ws.get("A2:Q", value_render_option="UNFORMATTED_VALUE") or []


def to_sheets(papers: list[Paper], date: str, cfg: dict, replace: bool = False) -> None:
    """Insert one day's papers (best first). With replace=True, that day's existing
    rows are removed first and your read?/rating values are carried over."""
    if not papers:
        return
    ws = _sheet(cfg, date)
    meta = _meta(ws)
    if "basicFilter" not in meta:
        _setup(ws, meta)

    keep, at = {}, 2                                   # 1-based row to insert at
    if replace:
        old = _data_rows(ws)
        idx = [i for i, r in enumerate(old) if r and _date_key(r[0]) == date]
        for i in idx:
            r = old[i] + [""] * len(SHEET_HEADER)
            keep[str(r[COL["arxiv_id"]])] = (r[COL["read?"]] or False, r[COL["my rating (1-5)"]])
        if idx:
            at = idx[0] + 2
            ws.spreadsheet.batch_update({"requests": [
                {"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "ROWS",
                                               "startIndex": i + 1, "endIndex": i + 2}}}
                for i in sorted(idx, reverse=True)]})
        print(f"    replacing {len(idx)} existing rows for {date}")

    rows = [_row(p, date, *keep.get(p.id, (False, ""))) for p in papers]
    ws.insert_rows(rows, row=at, value_input_option="USER_ENTERED")
    r0 = at - 1
    ws.spreadsheet.batch_update({"requests": _rows_reqs(ws, r0, r0 + len(rows),
                                                        [r0 + len(rows) - 1])})


def reformat(cfg: dict, date: str) -> None:
    """Re-apply the layout to the tab holding `date`, without changing any data."""
    ws = _sheet(cfg, date)
    _setup(ws, _meta(ws))
    rows = _data_rows(ws)
    n = len(rows)
    if not n:
        return
    dates = [_date_key(r[0]) if r else "" for r in rows]
    ends = [i + 1 for i in range(n) if i == n - 1 or dates[i] != dates[i + 1]]
    ws.spreadsheet.batch_update({"requests": _rows_reqs(ws, 1, 1 + n, ends)})
    print(f"    re-applied layout to {n} rows across {len(ends)} days in tab '{ws.title}'")


# ── Saved results (so the Sheet can be rebuilt without re-running Claude) ──
RESULTS_DIR = "results"
KEEP_RESULT_DAYS = 60


def save_results(papers: list[Paper], date: str, folder: str = RESULTS_DIR) -> str:
    os.makedirs(folder, exist_ok=True)
    slim = []
    for p in papers:
        d = asdict(p)
        d.pop("intro", None)           # big and not needed for the Sheet
        d["abstract"] = d["abstract"][:1500]
        slim.append(d)
    path = os.path.join(folder, f"{date}.json")
    with open(path, "w") as f:
        json.dump(slim, f, ensure_ascii=False, indent=1)
    cutoff = (dt.date.fromisoformat(date) - dt.timedelta(days=KEEP_RESULT_DAYS)).isoformat()
    for name in os.listdir(folder):
        if name.endswith(".json") and name[:-5] < cutoff:
            os.remove(os.path.join(folder, name))
    return path


def load_results(date: str | None = None, folder: str = RESULTS_DIR) -> tuple[str, list[Paper]]:
    files = sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json")) \
        if os.path.isdir(folder) else []
    if not files:
        raise FileNotFoundError(f"no saved results in {folder}/ yet (run the full digest once)")
    date = date or files[-1]
    with open(os.path.join(folder, f"{date}.json")) as f:
        papers = [Paper(**d) for d in json.load(f)]
    return date, papers


def liked_from_sheets(cfg: dict) -> list[str]:
    """Titles you rated highly → used as extra positive examples for scoring."""
    fb = cfg.get("feedback", {})
    if not fb.get("enabled") or not os.environ.get("GOOGLE_CREDS"):
        return []
    try:
        sh = _spreadsheet()
        name = cfg.get("delivery", {}).get("sheet_worksheet", "papers")
        # newest tabs first: "2026-10", "2026-09", ... then the legacy single tab
        tabs = sorted((w for w in sh.worksheets() if TAB_RE.match(w.title)),
                      key=lambda w: w.title, reverse=True)
        tabs += [w for w in sh.worksheets() if w.title == name]
        records = []
        for w in tabs[:6]:                          # last ~6 months is plenty
            records += w.get_all_records(expected_headers=["title", "my rating (1-5)"])
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
