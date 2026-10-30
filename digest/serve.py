"""Local web server: the same site, but serving EVERY paper in the database, straight from
SQLite (no rebuild needed after a run), with ratings and bookmarks saved directly to the DB.

    python run.py --serve [--host 127.0.0.1] [--port 8000]
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import hashlib
import http.server
import json
import mimetypes
import os
import re
import sqlite3
import threading

from . import db, site


class _Cache:
    """Index payload (gzipped) rebuilt only when papers change (a daily run, a prune), not on
    every like or rating. Concurrent requests during a rebuild wait for it and share it."""

    def __init__(self, path: str):
        self.path, self.lock, self.rev, self.gz, self.etag = path, threading.Lock(), None, b"", ""

    def index_gz(self) -> tuple[bytes, str]:
        """(gzipped index, ETag). The ETag lets browsers keep the index until the next run."""
        with self.lock:
            con = db.connect(self.path)
            try:
                rev = db.papers_rev(con)
                if rev != self.rev:
                    days = {r["date"]: dict(r) for r in con.execute("SELECT * FROM days")}
                    body = json.dumps(
                        site.index_payload(db.all_papers(con, cols=db.INDEX_COLS), days, db.updated_at(con)),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                    self.gz, self.rev = gzip.compress(body, 5), rev
                    self.etag = '"%s"' % hashlib.sha1(self.gz).hexdigest()[:16]
            finally:
                con.close()
            return self.gz, self.etag


class _Lru:
    """A small thread-safe LRU of built responses. Keys include the papers revision, so a run
    makes old entries unreachable and they age out."""

    def __init__(self, size: int):
        self.size, self.lock, self.d = size, threading.Lock(), collections.OrderedDict()

    def get(self, k):
        with self.lock:
            if k in self.d:
                self.d.move_to_end(k)
            return self.d.get(k)

    def put(self, k, v):
        with self.lock:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.size:
                self.d.popitem(last=False)
        return v


def _brief(con, pid: str) -> dict:
    r = con.execute("SELECT title, date FROM papers WHERE id=?", (pid,)).fetchone()
    return {"title": r["title"], "date": r["date"]} if r else {}


def _push_to_zotero(db_path: str, pid: str, cfg: dict) -> None:
    """Runs in a background thread after a like/bookmark; failures are retried by --zotero-sync."""
    try:
        from . import zotero

        if not zotero.enabled(cfg) or not zotero.configured():
            return
        con = db.connect(db_path)
        try:
            zotero.push(con, pid, cfg)
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        print(f"zotero push failed for {pid}: {e}  (retry: python run.py --zotero-sync)")


def _explain_in_background(db_path: str, pid: str, cfg: dict, llm) -> None:
    """Runs in a thread after "Explain this paper"; the paper is already claimed (pending)."""
    con = db.connect(db_path)
    try:
        from . import explain

        x = explain.explain(con, pid, cfg, llm)
        print(f"explained {pid} (${x['cost']:.3f})")
    except Exception as e:  # noqa: BLE001  (explain() already marked it failed)
        print(f"explaining {pid} failed: {e}")
    finally:
        con.close()


def make_handler(db_path: str, title: str, cfg: dict | None = None, llm=None):
    """`llm`: the Claude client for explanations; made on first use from ANTHROPIC_API_KEY."""
    cfg = cfg or {}
    xcfg = cfg.get("explain", {})
    can_explain = xcfg.get("enabled", True) and (llm is not None or bool(os.environ.get("ANTHROPIC_API_KEY")))
    claude = {"llm": llm, "lock": threading.Lock()}

    def request_explanation(con, pid: str) -> tuple[dict, int]:
        if not can_explain:
            return {"error": "explanations are off here (explain.enabled, or no ANTHROPIC_API_KEY)"}, 403
        if not con.execute("SELECT 1 FROM papers WHERE id = ?", (pid,)).fetchone():
            return {"error": "paper not in the database"}, 404
        x = db.get_explanation(con, pid)
        if x and x["status"] in ("done", "pending"):
            return x, 200
        budget = xcfg.get("daily_budget_usd", 5)
        _, spent = db.explain_spent(con, dt.date.today().isoformat())
        if budget and spent >= budget:
            return {"error": f"today's explanation budget (${budget}) is used up; try tomorrow"}, 429
        if db.claim_explanation(con, pid):  # False: someone else's click got there first
            with claude["lock"]:
                if claude["llm"] is None:
                    from .llm import LLM

                    claude["llm"] = LLM()
            threading.Thread(
                target=_explain_in_background, args=(db_path, pid, cfg, claude["llm"]), daemon=True
            ).start()
        return db.get_explanation(con, pid), 202

    min_rating = cfg.get("feedback", {}).get("min_rating", 4)
    cache = _Cache(db_path)
    vectors = None  # embed.Cache, created on first use (numpy/fastembed import is lazy)
    taste = {"lock": threading.Lock(), "key": None, "body": b""}
    days = _Lru(64)  # built day files (~0.5 MB gzipped each), so a popular day is built once
    about = site.about_html(cfg) if cfg.get("delivery", {}).get("about") else ""
    page = site.page_html(title, local=True, about=about).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, body: bytes, ctype: str, gz: bool = False, code: int = 200, etag: str = ""):
            if etag and etag in self.headers.get("If-None-Match", ""):
                self.send_response(304)  # the browser's copy is current: no body
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")  # cache, but revalidate every time
            if etag:
                self.send_header("ETag", etag)
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(json.dumps(obj).encode(), "application/json", code=code)

        def do_GET(self):
            nonlocal vectors
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self._send(page, "text/html; charset=utf-8")
            if path == "/data/index.json":
                body, etag = cache.index_gz()
                return self._send(body, "application/json", gz=True, etag=etag)
            m = re.fullmatch(r"/data/d/(\d{4}-\d{2}-\d{2})\.json", path)
            if m:
                con = db.connect(db_path)
                try:
                    # a day only changes with a run, so the papers revision tags it too
                    etag = '"%s-%s-%s"' % (m[1], *db.papers_rev(con))
                    if etag in self.headers.get("If-None-Match", ""):
                        return self._send(b"", "application/json", etag=etag)
                    gz = days.get(etag)
                    if gz is None:
                        shard = site.shards_for(db.all_papers(con, m[1])).get(m[1], {})
                        body = json.dumps(shard, ensure_ascii=False, separators=(",", ":")).encode()
                        gz = days.put(etag, gzip.compress(body, 5))
                finally:
                    con.close()
                if "gzip" in self.headers.get("Accept-Encoding", ""):  # every browser
                    return self._send(gz, "application/json", gz=True, etag=etag)
                return self._send(gzip.decompress(gz), "application/json", etag=etag)
            if path == "/api/state":
                con = db.connect(db_path)
                try:
                    return self._json(
                        {"ratings": db.ratings(con), "bookmarks": db.bookmarks(con), "likes": db.likes(con),
                         "explained": db.explained(con), "explain_on": can_explain}
                    )
                finally:
                    con.close()
            m = re.fullmatch(r"/data/x/([\w./-]+)\.json", path)
            if m:  # one paper's explanation; polled while it's being written
                con = db.connect(db_path)
                try:
                    x = db.get_explanation(con, m[1])
                finally:
                    con.close()
                return self._json(x) if x else self._json({"error": "no explanation"}, 404)
            if path in ("/api/taste", "/api/similar"):
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)
                try:
                    from . import embed

                    vectors = vectors or embed.Cache(db_path)
                    con = db.connect(db_path)
                    try:
                        ix = vectors.get(con)
                        if path == "/api/taste":
                            # everyone shares the picks, so one result serves every visitor
                            # until a pick or a new day's vectors change it
                            key = (vectors.stamp, embed.reference_ids(con, min_rating))
                            with taste["lock"]:
                                if taste["key"] != key:
                                    scores = embed.taste_scores(con, ix, min_rating=min_rating)
                                    taste.update(key=key, body=json.dumps(scores).encode())
                                body = taste["body"]
                            return self._send(body, "application/json")
                        pid = q.get("id", [""])[0]
                        return self._json(
                            [{"id": i, "sim": s, **_brief(con, i)} for i, s in embed.similar(con, pid, index=ix)]
                        )
                    finally:
                        con.close()
                except Exception as e:  # noqa: BLE001  (missing numpy, locked DB, ...)
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            name = os.path.basename(path)
            f = os.path.join(site.ASSETS, name)
            if name and os.path.isfile(f) and name != "index.html":
                ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
                with open(f, "rb") as fh:
                    return self._send(fh.read(), ctype)
            self._send(b"not found", "text/plain", code=404)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                pid = str(body["id"])
                if not re.fullmatch(r"\d{4}\.\d{4,5}|[a-z.\-]+/\d{7}", pid):
                    raise ValueError("bad id")
                con = db.connect(db_path)
                try:
                    if self.path == "/api/explain":
                        return self._json(*request_explanation(con, pid))
                    if self.path == "/api/rating":
                        db.set_rating(con, pid, int(body.get("rating", 0)))
                    elif self.path in ("/api/like", "/api/bookmark"):
                        on = bool(body.get("on"))
                        (db.set_like if self.path == "/api/like" else db.set_bookmark)(con, pid, on)
                        if on:  # un-liking never deletes from Zotero (you may have annotated it)
                            threading.Thread(
                                target=_push_to_zotero, args=(db_path, pid, cfg), daemon=True
                            ).start()
                    else:
                        return self._json({"error": "unknown endpoint"}, 404)
                finally:
                    con.close()
                self._json({"ok": True})
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                self._json({"error": str(e)}, 400)
            except sqlite3.OperationalError as e:  # still locked after db.connect's 30 s wait
                self._json({"error": f"database busy, try again ({e})"}, 503)

    return Handler


class _Server(http.server.ThreadingHTTPServer):
    """One thread per request. The default listen backlog (5) drops connections when a lab's
    worth of people open the page at once."""

    request_queue_size = 128


def serve(
    db_path: str, host: str = "127.0.0.1", port: int = 8000, title: str = "arXiv digest", cfg: dict | None = None
) -> None:
    # numpy (taste, similar papers) would otherwise spread each request over every core, which
    # on a shared server competes with everyone's jobs; set before numpy is first imported
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "4")
    db.connect(db_path).close()  # make sure the schema exists (and switch it to WAL)
    httpd = _Server((host, port), make_handler(db_path, title, cfg))
    print(f"serving all papers from {db_path} at http://{host}:{port}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
