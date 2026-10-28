"""Local web server: the same site, but serving EVERY paper in the database, straight from
SQLite (no rebuild needed after a run), with ratings and bookmarks saved directly to the DB.

    python run.py --serve [--host 127.0.0.1] [--port 8000]
"""
from __future__ import annotations

import gzip
import http.server
import json
import mimetypes
import os
import re
import threading

from . import db, site


class _Cache:
    """Index payload (gzipped) rebuilt only when the database file changes."""

    def __init__(self, path: str):
        self.path, self.lock, self.mtime, self.gz = path, threading.Lock(), None, b""

    def index_gz(self) -> bytes:
        with self.lock:
            m = os.stat(self.path).st_mtime_ns
            if m != self.mtime:
                con = db.connect(self.path)
                days = {r["date"]: dict(r) for r in con.execute("SELECT * FROM days")}
                body = json.dumps(
                    site.index_payload(db.all_papers(con), days),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
                self.gz, self.mtime = gzip.compress(body, 5), m
                con.close()
            return self.gz


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


def make_handler(db_path: str, title: str, cfg: dict | None = None):
    cfg = cfg or {}
    min_rating = cfg.get("feedback", {}).get("min_rating", 4)
    cache = _Cache(db_path)
    vectors = None  # embed.Cache, created on first use (numpy/fastembed import is lazy)
    page = site.page_html(title, local=True).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, body: bytes, ctype: str, gz: bool = False, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
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
                return self._send(cache.index_gz(), "application/json", gz=True)
            m = re.fullmatch(r"/data/d/(\d{4}-\d{2}-\d{2})\.json", path)
            if m:
                con = db.connect(db_path)
                try:
                    shard = site.shards_for(db.all_papers(con, m[1])).get(m[1], {})
                finally:
                    con.close()
                return self._send(
                    json.dumps(shard, ensure_ascii=False, separators=(",", ":")).encode(),
                    "application/json",
                )
            if path == "/api/state":
                con = db.connect(db_path)
                try:
                    return self._json(
                        {"ratings": db.ratings(con), "bookmarks": db.bookmarks(con), "likes": db.likes(con)}
                    )
                finally:
                    con.close()
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
                            return self._json(embed.taste_scores(con, ix, min_rating=min_rating))
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

    return Handler


def serve(
    db_path: str, host: str = "127.0.0.1", port: int = 8000, title: str = "arXiv digest", cfg: dict | None = None
) -> None:
    db.connect(db_path).close()  # make sure the schema exists
    httpd = http.server.ThreadingHTTPServer((host, port), make_handler(db_path, title, cfg))
    print(f"serving all papers from {db_path} at http://{host}:{port}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
