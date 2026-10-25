"""Push liked / bookmarked papers to your Zotero library (Zotero Web API v3).

Each paper becomes a `preprint` item in one collection, with a child note (TL;DR, why-you,
scores) and a linked-URL attachment to the arXiv PDF. The arXiv id → Zotero item key mapping is
kept in the local DB, so nothing is pushed twice; a paper already in your library (added by
hand) is linked and filed into the collection instead of duplicated. Un-liking never deletes
from Zotero, since you may have annotated the item.

    python run.py --zotero-sync      # check the key, push everything not yet in Zotero
"""
from __future__ import annotations

import datetime as dt
import html
import os
import threading
import time
import uuid

import requests

from . import db
from .env import load_env
from .site import clean_name

API = "https://api.zotero.org"
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
_lock = threading.Lock()  # one push at a time: a like + bookmark on the same paper → one item
_user: str | None = None


def configured() -> bool:
    """True once ZOTERO_API_KEY is set. Re-reads .env while it's missing, so adding the key
    takes effect without restarting the local server."""
    if not os.environ.get("ZOTERO_API_KEY", "").strip():
        load_env(ENV_FILE)
    return bool(os.environ.get("ZOTERO_API_KEY", "").strip())


def _headers(write_token: str | None = None) -> dict:
    h = {"Zotero-API-Key": os.environ["ZOTERO_API_KEY"].strip(), "Zotero-API-Version": "3"}
    if write_token:
        h["Zotero-Write-Token"] = write_token  # makes a retried POST idempotent
    return h


def _req(
    method: str, path: str, write_token: str | None = None, headers: dict | None = None, **kw
) -> requests.Response:
    """HTTP call that honours Zotero's Backoff / Retry-After on 429 and 503."""
    for attempt in range(4):
        h = {**_headers(write_token), **(headers or {})}
        r = requests.request(method, API + path, headers=h, timeout=20, **kw)
        wait = r.headers.get("Retry-After") or r.headers.get("Backoff")
        if r.status_code in (429, 503) and attempt < 3:
            time.sleep(min(float(wait or 2 ** attempt), 60))
            continue
        if r.status_code == 403:
            raise RuntimeError("Zotero refused the key (403): check ZOTERO_API_KEY and that it has write access")
        r.raise_for_status()
        return r
    return r


def whoami() -> dict:
    """{'userID': ..., 'username': ..., 'access': {...}} for the configured key."""
    return _req("GET", "/keys/current").json()


def user_id() -> str:
    global _user
    _user = _user or os.environ.get("ZOTERO_USER_ID", "").strip() or str(whoami()["userID"])
    return _user


def _post_items(items: list[dict]) -> list[str]:
    r = _req("POST", f"/users/{user_id()}/items", write_token=uuid.uuid4().hex, json=items)
    res = r.json()
    if res.get("failed"):
        raise RuntimeError(f"Zotero rejected the item: {res['failed']}")
    return [res["successful"][str(n)]["key"] for n in range(len(items))]


def collection_key(con, name: str) -> str:
    """Key of the collection called `name` (created if missing), cached in the DB."""
    row = con.execute("SELECT v FROM meta WHERE k = ?", (f"zotero_collection:{name}",)).fetchone()
    if row:
        return row["v"]
    key, start = None, 0
    while key is None:
        page = _req("GET", f"/users/{user_id()}/collections", params={"limit": 100, "start": start}).json()
        key = next((c["key"] for c in page if c["data"]["name"] == name), None)
        if len(page) < 100:
            break
        start += 100
    if key is None:
        r = _req("POST", f"/users/{user_id()}/collections", write_token=uuid.uuid4().hex, json=[{"name": name}])
        key = r.json()["successful"]["0"]["key"]
    with con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"zotero_collection:{name}", key))
    return key


def _creator(name: str) -> dict:
    parts = clean_name(name).split()
    if len(parts) < 2:
        return {"creatorType": "author", "name": " ".join(parts)}
    return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}


def item_json(p: dict, collection: str, tag: str) -> dict:
    """A Zotero `preprint` item (fields as in api.zotero.org/items/new?itemType=preprint)."""
    pid = p["id"]
    return {
        "itemType": "preprint",
        "title": p["title"],
        "creators": [_creator(a) for a in p["authors"]],
        "abstractNote": p["abstract"] or "",
        "repository": "arXiv",
        "archiveID": f"arXiv:{pid}",
        "date": p["date"],
        "DOI": f"10.48550/arXiv.{pid}",
        "url": f"https://arxiv.org/abs/{pid}",
        "libraryCatalog": "arXiv.org",
        "extra": f"arXiv: {pid}\n" + (f"Code: {p['code_url']}\n" if p.get("code_url") else ""),
        "tags": [{"tag": t} for t in [tag, *p["tags"]] if t],
        "collections": [collection],
        "relations": {},
    }


def note_html(p: dict) -> str:
    e = html.escape
    parts = ["<h2>arxiv-digest</h2>"]
    if p.get("tldr"):
        parts.append(f"<p><b>TL;DR</b> {e(p['tldr'])}</p>")
    if p.get("why"):
        parts.append(f"<p><b>Why you</b> {e(p['why'])}</p>")
    elif p.get("reason"):
        parts.append(f"<p>{e(p['reason'])}</p>")
    parts.append(
        f"<p>Score {p['final']:.1f} · Quality {p['quality']:.1f} · Impact {p['impact'] or 0:.1f}"
        + (f" — {e(p['quality_note'])}" if p.get("quality_note") else "") + "</p>"
    )
    return "".join(parts)


def _find_existing(pid: str) -> dict | None:
    """An item you already have for this arXiv id (e.g. saved with the Zotero connector)."""
    found = _req("GET", f"/users/{user_id()}/items/top", params={"q": pid, "qmode": "everything", "limit": 25}).json()
    for it in found:
        d = it["data"]
        if pid in " ".join(str(d.get(f, "")) for f in ("url", "archiveID", "extra", "DOI")):
            return it
    return None


def push(con, pid: str, cfg: dict) -> str:
    """Make sure paper `pid` is in Zotero. Returns 'already', 'linked' or 'added'."""
    zc = cfg.get("zotero", {})
    with _lock:
        if con.execute("SELECT 1 FROM zotero WHERE id = ?", (pid,)).fetchone():
            return "already"
        p = db.get_paper(con, pid)
        if not p:
            raise RuntimeError(f"{pid} is not in the database")
        coll = collection_key(con, zc.get("collection", "arxiv-digest"))
        existing = _find_existing(pid)
        if existing:  # file your copy into the collection rather than making a duplicate
            key, d = existing["key"], existing["data"]
            if coll not in d.get("collections", []):
                _req("PATCH", f"/users/{user_id()}/items/{key}",
                     headers={"If-Unmodified-Since-Version": str(existing["version"])},
                     json={"collections": [*d.get("collections", []), coll]})
            status = "linked"
        else:
            key = _post_items([item_json(p, coll, zc.get("tag", "arxiv-digest"))])[0]
            children = []
            if zc.get("note", True):
                children.append({"itemType": "note", "parentItem": key, "note": note_html(p), "tags": [], "relations": {}})
            if zc.get("pdf_link", True):
                children.append({"itemType": "attachment", "parentItem": key, "linkMode": "linked_url",
                                 "title": "arXiv PDF", "url": f"https://arxiv.org/pdf/{pid}",
                                 "contentType": "application/pdf", "tags": [], "relations": {}})
            if children:
                _post_items(children)
            status = "added"
        with con:
            con.execute("INSERT OR REPLACE INTO zotero VALUES (?,?,?)", (pid, key, dt.date.today().isoformat()))
        return status


def pending(con) -> list[str]:
    """Liked or bookmarked papers that aren't in Zotero yet."""
    return [r[0] for r in con.execute(
        "SELECT id FROM likes UNION SELECT id FROM bookmarks EXCEPT SELECT id FROM zotero ORDER BY 1")]


def sync(con, cfg: dict) -> str:
    """Push every pending paper; returns a one-line summary."""
    counts: dict[str, int] = {}
    errors = []
    for pid in pending(con):
        try:
            s = push(con, pid, cfg)
            counts[s] = counts.get(s, 0) + 1
        except Exception as e:  # noqa: BLE001  keep going; the next sync retries it
            errors.append(f"{pid}: {e}")
    msg = ", ".join(f"{n} {s}" for s, n in counts.items()) or "nothing to push"
    return msg + (f"; {len(errors)} failed ({errors[0]})" if errors else "")
