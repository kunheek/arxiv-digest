"""Offline end-to-end test: fake RSS, fake arXiv HTML, fake Claude. Run: python -m pytest -q"""

import json
import os
import re
import sqlite3
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run  # noqa: E402
from digest import enrich as enrich_mod  # noqa: E402
from digest.fetch import parse_rss  # noqa: E402
from digest.llm import LLM  # noqa: E402

RSS = """<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
<channel><title>cs.CV updates on arXiv.org</title>
{items}
</channel></rss>"""
ITEM = """<item><title>{title}</title><link>https://arxiv.org/abs/{id}</link>
<description>arXiv:{id}v1 Announce Type: {atype}
Abstract: {abstract}</description>
<guid isPermaLink="false">oai:arXiv.org:{id}v1</guid>
<category>cs.CV</category><category>cs.LG</category>
<arxiv:announce_type>{atype}</arxiv:announce_type>
<dc:creator>{authors}</dc:creator></item>"""

PAPERS = [
    (
        "2610.00001",
        "Caching Attention for Fast Video DiT Inference",
        "Andrea Vedaldi, Jane Doe",
        "new",
        "We cache attention maps across denoising steps in video diffusion transformers. Code: https://github.com/x/fastdit.",
    ),
    (
        "2610.00002",
        "Feed-forward 4D Reconstruction from Monocular Video",
        "John Smith, Kim Lee",
        "cross",
        "A VGGT-style transformer that regresses dynamic pointmaps.",
    ),
    (
        "2610.00003",
        "Federated Learning for Medical Imaging",
        "A B, C D",
        "new",
        "We study federated learning for MRI segmentation.",
    ),
    ("2610.00004", "Replaced Old Paper", "X Y", "replace", "old"),
]


def make_rss():
    return RSS.format(
        items="\n".join(
            ITEM.format(id=i, title=t, authors=a, atype=ty, abstract=ab)
            for i, t, a, ty, ab in PAPERS
        )
    )


class FakeClient:
    """Mimics anthropic.Anthropic().messages.create."""

    def __init__(self):
        self.messages = self
        self.calls = []

    def create(self, model, max_tokens, system, messages, output_config=None):
        schema = output_config["format"]["schema"]
        assert output_config["format"]["type"] == "json_schema"
        assert "minimum" not in json.dumps(schema)  # unsupported by the API
        assert schema.get("additionalProperties") is False  # required by the API
        self.calls.append(model)
        content = messages[0]["content"]
        if "<paper id" in content:  # scoring
            ids = re.findall(r'<paper id="([^"]+)"', content)
            score = {"2610.00001": 9, "2610.00002": 8, "2610.00003": 1}
            # a reason with quotes in it -- the old text-parsing version broke on this
            data = {
                "papers": [
                    {
                        "id": i,
                        "score": score.get(i, 0),
                        "quality": {"2610.00001": 8, "2610.00002": 2}.get(i, 5),
                        "quality_note": "solid ablations",
                        "impact": {"2610.00003": 9}.get(i, 2),
                        "reason": 'uses "TeaCache"-style caching',
                        "tags": ["video-dit"],
                    }
                    for i in ids
                ]
            }
        else:  # summary
            data = {
                "tldr": "Does a thing fast.",
                "why": "Matches your DiT work.",
                "quality": 9,
                "quality_note": "convincing",
                "impact": 3,
            }
        usage = types.SimpleNamespace(
            input_tokens=1000,
            output_tokens=100,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return types.SimpleNamespace(
            stop_reason="end_turn",
            usage=usage,
            content=[types.SimpleNamespace(type="text", text=json.dumps(data))],
        )


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_zotero(monkeypatch, tmp_path):
    """Never let a test reach your real Zotero library: no key from the shell or .env, unless a
    test sets one itself (and patches `requests`)."""
    from digest import zotero
    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.setattr(zotero, "ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setattr(zotero, "_user", None)

    def no_network(*a, **kw):
        raise AssertionError("a test tried to call the real Zotero API")
    monkeypatch.setattr(zotero.requests, "request", no_network)
    real_load_env = run.load_env
    # run.main() loads the real .env; drop Zotero keys from it so background pushes stay off
    monkeypatch.setattr(run, "load_env", lambda path: (real_load_env(path), os.environ.pop("ZOTERO_API_KEY", None)))


def test_parse_rss():
    ps = parse_rss(make_rss())
    assert [p.id for p in ps] == [
        "2610.00001",
        "2610.00002",
        "2610.00003",
        "2610.00004",
    ]
    assert ps[0].authors == ["Andrea Vedaldi", "Jane Doe"]
    assert ps[1].announce_type == "cross"
    assert ps[0].abstract.startswith("We cache")


def test_institution_matching():
    from digest.fetch import Paper

    p = Paper(
        "1",
        "t",
        "a",
        [],
        [],
        "new",
        "l",
        affiliations=["Work submitted while at Stanford University"],
    )
    enrich_mod.match_institutions(
        p, ["Massachusetts Institute of Technology|MIT", "Stanford"]
    )
    assert p.inst_hits == ["Stanford"]  # 'submitted' must not trigger MIT


def test_end_to_end(tmp_path, monkeypatch):
    def fake_html(p, max_intro_chars=6000):
        p.affiliations = (
            ["University of Oxford"] if p.id == "2610.00001" else ["Some Lab"]
        )
        p.intro = "Introduction text."

    monkeypatch.setattr(enrich_mod, "fetch_html", fake_html)
    monkeypatch.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import numpy as np
    from digest import embed, zotero
    # no model download / network in tests: fake vectors, and Zotero treated as not configured
    monkeypatch.setattr(embed, "encode", lambda texts: np.eye(len(texts), 384, dtype=np.float32))
    monkeypatch.setattr(zotero, "configured", lambda: False)

    def fetcher(cats, types_):
        return [p for p in parse_rss(make_rss()) if p.announce_type in types_]

    client = FakeClient()
    llm = LLM(client)
    state = tmp_path / "seen.json"
    md = tmp_path / "digest.md"
    monkeypatch.setattr(
        run.deliver,
        "to_markdown",
        lambda top, d, s, path, f=run.deliver.to_markdown: f(top, d, s, str(md)),
    )
    # test uses its own watchlists so editing your real config.yaml never breaks it
    import yaml

    cfg = yaml.safe_load(open("config.yaml"))
    cfg["watch_authors"] = ["Andrea Vedaldi"]
    cfg["watch_institutions"] = ["University of Oxford|Oxford"]
    cfg["delivery"].update(site=False, publish=False, discord=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    args = [
        "--state",
        str(state),
        "--config",
        str(cfg_path),
        "--db",
        str(tmp_path / "digest.db"),
    ]
    assert run.main(args, llm=llm, fetcher=fetcher) == 0

    out = md.read_text()
    first = out.split("## 2.")[0]
    assert "Caching Attention" in first  # 9 + author bonus + inst bonus
    assert "👤 Andrea Vedaldi" in out and "🏛 University of Oxford" in out
    assert "github.com/x/fastdit" in out
    assert "Replaced Old Paper" not in out  # 'replace' filtered out
    assert "claude-haiku-5-5" in client.calls and "claude-sonnet-5-5" in client.calls
    seen = json.loads(state.read_text())
    assert set(seen) == {"2610.00001", "2610.00002", "2610.00003"}
    con = sqlite3.connect(str(tmp_path / "digest.db"))
    assert con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == \
        con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]          # today's papers embedded

    # second run same day: nothing new, no Claude calls
    n = len(client.calls)
    assert run.main(args, llm=llm, fetcher=fetcher) == 0
    assert len(client.calls) == n


def test_failed_batch_is_skipped_not_fatal():
    from digest.score import score_all
    import yaml

    cfg = yaml.safe_load(
        open(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.yaml",
            )
        )
    )
    papers = parse_rss(make_rss())[:3]

    class Flaky:
        cost = 0
        calls = 0

        def ask_structured(self, *a, **k):
            raise RuntimeError("boom")

    score_all(papers, cfg, Flaky(), [])  # must not raise
    assert all(p.interest == 0 for p in papers)


def test_env_file(tmp_path, monkeypatch):
    from digest.env import load_env

    env = tmp_path / ".env"
    env.write_text(
        '# comment\nexport ANTHROPIC_API_KEY="sk-ant-test"\nAUTO_PULL=0\n'
        "DISCORD_WEBHOOK=from-file\n"
    )
    for k in ["ANTHROPIC_API_KEY", "AUTO_PULL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK", "from-shell")  # real env wins over .env
    load_env(str(env))
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert os.environ["AUTO_PULL"] == "0"
    assert os.environ["DISCORD_WEBHOOK"] == "from-shell"


def test_quality_modulates_relevance():
    from digest.enrich import final_score
    from digest.fetch import Paper

    cfg = {
        "ranking": {
            "quality_weight": 0.3,
            "min_interest_for_bonus": 4,
            "author_bonus": 3,
            "institution_bonus": 1.5,
        }
    }
    mk = lambda i, q, **k: Paper(
        "1", "t", "a", [], [], "new", "l", interest=i, quality=q, **k
    )
    assert final_score(mk(8, 10), cfg) == 8.0  # top quality keeps full relevance
    assert final_score(mk(8, 0), cfg) == 5.6  # zero quality keeps 70%
    assert final_score(mk(0, 10), cfg) == 0.0  # quality can't rescue irrelevance
    assert final_score(mk(8, 5), cfg) == 6.8
    assert final_score(mk(8, 10, author_hits=["X"]), cfg) == 11.0
    assert (
        final_score(mk(2, 10, author_hits=["X"]), cfg) == 2.0
    )  # bonus gated by relevance
    # foundational papers are kept regardless of topic
    cfg["ranking"].update(impact_min=7, impact_weight=0.9)
    assert final_score(mk(0, 5, impact=9), cfg) == 8.1  # irrelevant but landmark
    assert final_score(mk(0, 5, impact=6), cfg) == 0.0  # below impact_min: no floor
    assert final_score(mk(8, 10, impact=9), cfg) == 8.1  # floor, not a sum


def test_ratings_roundtrip(tmp_path):
    from digest import db
    from digest.fetch import Paper

    con = db.connect(str(tmp_path / "d.db"))
    db.upsert(
        con,
        [
            Paper("1", "Loved", "a", [], [], "new", "l"),
            Paper("2", "Meh", "a", [], [], "new", "l"),
        ],
        "2026-10-16",
    )
    f = tmp_path / "ratings.json"
    f.write_text(json.dumps({"exported": "x", "ratings": {"1": 5, "2": 2, "3": 9}}))
    assert db.import_ratings(con, str(f)) == 2  # 9 is out of range → ignored
    assert db.liked_titles(con, 4, 10) == ["Loved"]
    f.write_text(json.dumps({"ratings": {"1": 0}}))  # 0 clears
    db.import_ratings(con, str(f))
    assert db.liked_titles(con, 4, 10) == []


def test_old_db_gets_quality_columns(tmp_path):
    import sqlite3
    from digest import db

    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TABLE papers (id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT NOT NULL, abstract TEXT, "
        "authors TEXT, affiliations TEXT, categories TEXT, tags TEXT, announce_type TEXT, link TEXT, "
        "code_url TEXT, interest REAL, final REAL, reason TEXT, tldr TEXT, why TEXT, author_hits TEXT, inst_hits TEXT)"
    )
    c.commit()
    c.close()
    cols = {r["name"] for r in db.connect(p).execute("PRAGMA table_info(papers)")}
    assert {"quality", "quality_note"} <= cols


def test_parse_affiliations():
    from bs4 import BeautifulSoup
    from digest.enrich import parse_affiliations

    contact = BeautifulSoup(
        '<div class="ltx_authors"><span class="ltx_creator"><span class="ltx_personname">A B</span>'
        '<span class="ltx_contact ltx_role_affiliation"><span class="ltx_contact_name">Affiliation: </span>'
        "Shanghai AI Laboratory  Beihang University\n</span>"
        '<span class="ltx_contact ltx_role_email"><span class="ltx_contact_name">Email: </span>a@b.c</span>'
        "</span></div>",
        "html.parser",
    )
    assert parse_affiliations(contact, ["A B"]) == [
        "Shanghai AI Laboratory",
        "Beihang University",
    ]
    plain = BeautifulSoup(
        '<div class="ltx_authors"><span class="ltx_personname"><span class="ltx_p">'
        '<span class="ltx_font_bold">Yi Li<sup>1</sup>, Jo Ng<sup>2</sup></span>Cornell University\n'
        "<sup>2</sup>University of Pennsylvania</span></span></div>",
        "html.parser",
    )
    assert parse_affiliations(plain, ["Yi Li", "Jo Ng"]) == [
        "Cornell University",
        "University of Pennsylvania",
    ]
    names_only = BeautifulSoup(
        '<div class="ltx_authors"><span class="ltx_personname">Yi Li</span>'
        '<span class="ltx_personname">Jo Ng</span></div>',
        "html.parser",
    )
    assert parse_affiliations(names_only, ["Yi Li", "Jo Ng"]) == []


def test_discord_is_a_single_summary_message(monkeypatch):
    from digest import deliver

    sent = []
    resp = types.SimpleNamespace(status_code=200, raise_for_status=lambda: None)
    monkeypatch.setattr(
        deliver.requests, "post", lambda url, json, timeout: sent.append(json) or resp
    )
    deliver.to_discord(
        "2026-10-17",
        {"total": 454, "on_site": 172, "top": 14},
        "http://hook",
        "https://u.github.io/r/#/?d=2026-10-17",
    )
    assert len(sent) == 1 and "embeds" not in sent[0]
    msg = sent[0]["content"]
    assert (
        "updated" in msg
        and "454" in msg
        and "172" in msg
        and "<https://u.github.io/r/#/?d=2026-10-17>" in msg
    )


def test_empty_feed_notifies_but_processed_day_does_not(tmp_path, monkeypatch):
    import yaml
    sent = []
    monkeypatch.setattr(run.deliver, "notify", lambda hook, text: sent.append(text))
    monkeypatch.setenv("DISCORD_WEBHOOK", "http://hook")
    monkeypatch.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = yaml.safe_load(open("config.yaml"))
    cfg["delivery"].update(site=False, publish=False, discord=True)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    args = ["--state", str(tmp_path / "seen.json"), "--config", str(cfg_path), "--db", str(tmp_path / "d.db")]
    assert run.main(args, llm=object(), fetcher=lambda *a: []) == 0          # empty feed
    assert len(sent) == 1 and "no new papers" in sent[0]
    seen = {p.id: "2026-10-19" for p in parse_rss(make_rss())}
    (tmp_path / "seen.json").write_text(json.dumps(seen))
    assert run.main(args, llm=object(), fetcher=lambda *a: parse_rss(make_rss())) == 0   # all seen
    assert len(sent) == 1                                                    # stays quiet


def _paper(pid, **kw):
    from digest.fetch import Paper
    return Paper(pid, f"Title {pid}", "abs", ["A B"], ["cs.CV"], "new", "l", **kw)


def test_prune_is_fifo_and_keeps_special_papers(tmp_path):
    from digest import db
    con = db.connect(str(tmp_path / "d.db"))
    for day, ids in (("2026-10-01", ["a1", "a2", "a3"]), ("2026-10-02", ["b1", "b2", "b3"]),
                     ("2026-10-03", ["c1", "c2", "c3"])):
        db.upsert(con, [_paper(i, final=5) for i in ids], day, {"total": 3, "cost": 0, "calls": 0})
    db.set_rating(con, "a1", 4)          # rated → never deleted
    db.set_bookmark(con, "b2", True)     # bookmarked → never deleted
    assert db.prune(con, 5) == 2         # 7 ordinary papers, limit 5 → drop the 2 oldest ordinary
    left = {r["id"] for r in con.execute("SELECT id FROM papers")}
    assert {"a1", "b2"} <= left and not ({"a2", "a3"} & left)
    assert {"b1", "b3", "c1", "c2", "c3"} <= left
    assert db.prune(con, 5) == 0 and db.prune(con, 0) == 0
    db.prune(con, 1)
    assert "2026-10-03" in {r["date"] for r in con.execute("SELECT date FROM days")}
    assert {"a1", "b2"} <= {r["id"] for r in con.execute("SELECT id FROM papers")}


def test_import_bookmarks_and_ratings(tmp_path):
    from digest import db
    con = db.connect(str(tmp_path / "d.db"))
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"ratings": {"x": 5}, "bookmarks": ["y"]}))
    db.import_ratings(con, str(f))
    assert db.ratings(con) == {"x": 5} and db.bookmarks(con) == ["y"]


def test_local_server_serves_everything_and_saves_state(tmp_path):
    import gzip
    import http.server
    import threading
    import urllib.request
    from digest import db, serve
    path = str(tmp_path / "d.db")
    con = db.connect(path)
    db.upsert(con, [_paper("2610.0001", interest=0, final=0), _paper("2610.0002", interest=9, final=9)],
              "2026-10-16", {"total": 2, "cost": 0, "calls": 0})
    con.close()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(path, "T"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        idx = json.loads(gzip.decompress(urllib.request.urlopen(base + "/data/index.json").read()))
        assert {r[0] for r in idx["papers"]} == {"2610.0001", "2610.0002"}          # includes irrelevant paper
        shard = json.loads(urllib.request.urlopen(base + "/data/d/2026-10-16.json").read())
        assert set(shard) == {"2610.0001", "2610.0002"}
        post = lambda p, b: urllib.request.urlopen(urllib.request.Request(
            base + p, json.dumps(b).encode(), {"Content-Type": "application/json"})).read()
        post("/api/rating", {"id": "2610.0002", "rating": 5})
        post("/api/bookmark", {"id": "2610.0001", "on": True})
        post("/api/like", {"id": "2610.0002", "on": True})
        st = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert st == {"ratings": {"2610.0002": 5}, "bookmarks": ["2610.0001"], "likes": ["2610.0002"]}
        assert b"app.js" in urllib.request.urlopen(base + "/").read()
        assert urllib.request.urlopen(base + "/app.js").status == 200
    finally:
        httpd.shutdown()


def test_public_site_is_capped_but_db_is_not(tmp_path):
    from digest import db, site
    con = db.connect(str(tmp_path / "d.db"))
    for i, day in enumerate(["2026-10-01", "2026-10-02", "2026-10-03"]):
        db.upsert(con, [_paper(f"2610.0{i}00{j}", interest=5, final=5) for j in range(2)], day,
                  {"total": 2, "cost": 0, "calls": 0})
    out = str(tmp_path / "site")
    assert site.build(con, out, "T", 2, 11, max_papers=4) == 4          # newest 4 of 6
    idx = json.load(open(os.path.join(out, "data", "index.json")))
    assert {r[1] for r in idx["papers"]} == {"2026-10-02", "2026-10-03"}
    assert con.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 6   # DB untouched
    assert not any(f.endswith(".db") for _, _, fs in os.walk(out) for f in fs)


def test_public_site_mirrors_local_preferences(tmp_path):
    from digest import db, site
    con = db.connect(str(tmp_path / "d.db"))
    for i, day in enumerate(["2026-10-01", "2026-10-02", "2026-10-03"]):
        db.upsert(con, [_paper(f"2610.0{i}00{j}", interest=5, final=5) for j in range(2)], day,
                  {"total": 2, "cost": 0, "calls": 0})
    db.set_rating(con, "2610.00000", 5)       # oldest day: outside the cap, but rated
    db.set_bookmark(con, "2610.00001", True)
    out = str(tmp_path / "site")
    site.build(con, out, "T", 2, 11, max_papers=2)
    prefs = json.load(open(os.path.join(out, "data", "prefs.json")))
    assert prefs == {"ratings": {"2610.00000": 5}, "bookmarks": ["2610.00001"], "likes": []}
    idx = json.load(open(os.path.join(out, "data", "index.json")))
    assert {"2610.00000", "2610.00001"} <= {r[0] for r in idx["papers"]}
    assert "export" not in open(os.path.join(out, "index.html")).read().lower()


def test_likes_protected_and_used_as_examples(tmp_path):
    from digest import db
    con = db.connect(str(tmp_path / "d.db"))
    db.upsert(con, [_paper("2610.00001"), _paper("2610.00002")], "2026-10-01",
              {"total": 2, "cost": 0, "calls": 0})
    db.set_like(con, "2610.00001", True)
    assert db.likes(con) == ["2610.00001"]
    assert len(db.liked_titles(con)) == 1
    db.prune(con, 1)
    assert con.execute("SELECT COUNT(*) FROM papers WHERE id='2610.00001'").fetchone()[0] == 1
    db.set_like(con, "2610.00001", False)
    assert db.likes(con) == []


def test_bookmarks_used_as_examples(tmp_path):
    from digest import db
    con = db.connect(str(tmp_path / "d.db"))
    db.upsert(con, [_paper("2610.00001"), _paper("2610.00002")], "2026-10-01",
              {"total": 2, "cost": 0, "calls": 0})
    db.set_bookmark(con, "2610.00001", True)
    db.set_like(con, "2610.00001", True)
    assert len(db.liked_titles(con)) == 1  # bookmarked + liked counts once
    db.set_bookmark(con, "2610.00002", True)
    assert len(db.liked_titles(con)) == 2
    db.set_rating(con, "2610.00002", 2)  # bookmarked but rated low → not an example
    assert len(db.liked_titles(con)) == 1


def test_embeddings_taste_and_similar(tmp_path, monkeypatch):
    import numpy as np
    from digest import db, embed
    con = db.connect(str(tmp_path / "d.db"))
    papers = [_paper(f"2610.{i:05d}") for i in range(12)]
    db.upsert(con, papers, "2026-10-01", {"total": 12, "cost": 0, "calls": 0})
    # two topics: even ids ≈ axis 0, odd ids ≈ axis 1
    def fake(texts):
        out = []
        for t in texts:
            k = int(t.split("#")[1][:4]) if "#" in t else 0
            v = np.array([1.0, 0.05 * k] if k % 2 == 0 else [0.05 * k, 1.0], np.float32)
            out.append(v / np.linalg.norm(v))
        return np.pad(np.array(out), ((0, 0), (0, 382)))
    monkeypatch.setattr(embed, "encode", fake)
    for p in papers:
        con.execute("UPDATE papers SET title=? WHERE id=?", (f"x #{int(p.id[5:]):04d}", p.id))
    assert embed.embed_missing(con) == 12 and embed.embed_missing(con) == 0
    assert embed.taste_scores(con) == {}                       # no picks yet
    for i in (0, 2, 4, 6):
        db.set_like(con, f"2610.{i:05d}", True)
    ts = embed.taste_scores(con)
    even = [v for k, v in ts.items() if int(k[5:]) % 2 == 0]
    odd = [v for k, v in ts.items() if int(k[5:]) % 2 == 1]
    assert min(even) > max(odd)                                # liked topic ranks above the other
    sim = embed.similar(con, "2610.00000")
    assert all(int(i[5:]) % 2 == 0 for i, _ in sim[:5])
    db.prune(con, 3)
    assert con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]


class FakeZotero:
    """Just enough of api.zotero.org for zotero.py: collections, item search, item writes."""

    def __init__(self, library=None):
        self.collections, self.items, self.calls = [], dict(library or {}), []

    def __call__(self, method, url, headers=None, timeout=None, params=None, json=None):
        path = url.split("api.zotero.org")[1]
        self.calls.append((method, path, json))
        assert headers["Zotero-API-Key"] == "k" and headers["Zotero-API-Version"] == "3"
        if method == "POST":
            assert headers.get("Zotero-Write-Token")
        r = types.SimpleNamespace(status_code=200, headers={}, raise_for_status=lambda: None)
        if path.endswith("/collections") and method == "GET":
            r.json = lambda: [{"key": k, "data": {"name": n}} for k, n in self.collections]
        elif path.endswith("/collections"):
            self.collections.append(("COLL0001", json[0]["name"]))
            r.json = lambda: {"successful": {"0": {"key": "COLL0001"}}, "failed": {}}
        elif path.endswith("/items/top"):
            # like the real API: quick search matches titles (and creators/year), never URL or ids
            assert params["qmode"] == "titleCreatorYear"
            q = params["q"].lower()
            r.json = lambda: [{"key": k, "version": 3, "data": d} for k, d in self.items.items()
                              if d.get("title") and all(w in d["title"].lower() for w in q.split())]
        elif path.endswith("/items") and method == "POST":
            keys = {}
            for n, it in enumerate(json):
                k = f"ITEM{len(self.items):04d}"
                self.items[k] = it
                keys[str(n)] = {"key": k}
            r.json = lambda: {"successful": keys, "failed": {}}
        elif method == "PATCH":
            assert headers["If-Unmodified-Since-Version"] == "3"
            self.items[path.rsplit("/", 1)[1]].update(json)
            r.json = lambda: {}
        return r


def test_zotero_push_adds_once_and_links_existing(tmp_path, monkeypatch):
    from digest import db, zotero
    monkeypatch.setenv("ZOTERO_API_KEY", "k")
    monkeypatch.setenv("ZOTERO_USER_ID", "42")
    monkeypatch.setattr(zotero, "_user", None)
    fake = FakeZotero({"MINE0001": {"itemType": "preprint", "title": "Title 2610.00002",
                                    "url": "http://arxiv.org/abs/2610.00002", "collections": []}})
    monkeypatch.setattr(zotero.requests, "request", fake)
    con = db.connect(str(tmp_path / "d.db"))
    db.upsert(con, [_paper("2610.00001", tags=["3D"], tldr="short"), _paper("2610.00002")], "2026-10-01",
              {"total": 2, "cost": 0, "calls": 0})
    db.set_like(con, "2610.00001", True)
    db.set_bookmark(con, "2610.00002", True)
    assert zotero.pending(con) == ["2610.00001", "2610.00002"]
    cfg = {"zotero": {"collection": "arxiv-digest", "tag": "arxiv-digest"}}
    assert zotero.sync(con, cfg) == "1 added, 1 linked"
    item = next(d for d in fake.items.values() if d.get("archiveID") == "arXiv:2610.00001")
    assert item["itemType"] == "preprint" and item["collections"] == ["COLL0001"]
    assert {"tag": "3D"} in item["tags"] and item["creators"][0] == {"creatorType": "author", "firstName": "A", "lastName": "B"}
    kids = [d for d in fake.items.values() if d.get("parentItem")]
    assert {k["itemType"] for k in kids} == {"note", "attachment"} and "short" in next(k["note"] for k in kids if k["itemType"] == "note")
    assert fake.items["MINE0001"]["collections"] == ["COLL0001"]           # your copy filed, not duplicated
    n = len(fake.calls)
    assert zotero.sync(con, cfg) == "nothing to push" and zotero.push(con, "2610.00001", cfg) == "already"
    assert len(fake.calls) == n                                              # no API calls once synced


def test_zotero_not_configured_is_a_noop(tmp_path, monkeypatch):
    from digest import zotero
    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    monkeypatch.setattr(zotero, "ENV_FILE", str(tmp_path / "missing.env"))
    assert not zotero.configured()
    (tmp_path / "late.env").write_text("ZOTERO_API_KEY=added-later\n")   # key added while the server runs
    monkeypatch.setattr(zotero, "ENV_FILE", str(tmp_path / "late.env"))
    assert zotero.configured()


def test_like_on_local_server_pushes_to_zotero(tmp_path, monkeypatch):
    import http.server, threading, urllib.request
    from digest import db, serve, zotero
    path = str(tmp_path / "d.db")
    con = db.connect(path)
    db.upsert(con, [_paper("2610.00001")], "2026-10-01", {"total": 1, "cost": 0, "calls": 0})
    con.close()
    pushed = threading.Event()
    monkeypatch.setattr(zotero, "configured", lambda: True)
    monkeypatch.setattr(zotero, "push", lambda con, pid, cfg: pushed.set() or "added")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(path, "T", {}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        post = lambda p, b: json.loads(urllib.request.urlopen(urllib.request.Request(
            base + p, json.dumps(b).encode(), {"Content-Type": "application/json"})).read())
        assert post("/api/like", {"id": "2610.00001", "on": False}) == {"ok": True}
        assert not pushed.wait(0.3)                                           # un-like: no push
        assert post("/api/like", {"id": "2610.00001", "on": True}) == {"ok": True}
        assert pushed.wait(5)
    finally:
        httpd.shutdown()


def test_bibtex_entry():
    from digest import bibtex
    p = {"id": "2610.01234", "date": "2026-10-20", "title": "The  Sheaf & Graph\nNetworks",
         "authors": ['Kurt G\\"odel', "Ada Lovelace"], "categories": ["cs.LG", "math.AT"]}
    e = bibtex.entry(p)
    assert e.startswith("@article{godel2026sheaf,")
    assert "title={The Sheaf \\& Graph Networks}" in e and "author={Kurt Gödel and Ada Lovelace}" in e
    assert "journal={arXiv preprint arXiv:2610.01234}" in e and "year={2026}" in e
    assert bibtex.title("RayCo4D: Ray-Conditioned 4D Motion on $\\mathrm{SE}(3)$ with NeRF/3DGS") == \
        "{RayCo4D:} Ray-Conditioned {4D} Motion on $\\mathrm{SE}(3)$ with {NeRF}/{3DGS}"


def test_taste_never_scores_a_reference_paper_against_itself(tmp_path):
    import numpy as np
    from digest import db, embed
    con = db.connect(str(tmp_path / "d.db"))
    ids = [f"2610.{i:05d}" for i in range(6)]
    db.upsert(con, [_paper(i) for i in ids], "2026-10-01", {"total": 6, "cost": 0, "calls": 0})
    rng = np.random.default_rng(0)
    for i in ids:
        v = rng.normal(size=384).astype(np.float32)
        con.execute("INSERT INTO vectors VALUES (?,?)", (i, (v / np.linalg.norm(v)).astype(np.float16).tobytes()))
    con.commit()
    for i in ids[:3]:
        db.set_like(con, i, True)
    db.set_rating(con, ids[3], 1)                                  # disliked
    ix = embed.Index(con)
    raw = embed._taste_loo(ix, [ids[3]], ids[:3], [ids[3]])[0]
    # with only itself as a negative, leaving it out means no negative penalty at all
    assert abs(raw - embed.taste_raw(ix.rows([ids[3]]), ix.rows(ids[:3]), ix.rows([]))[0]) < 1e-6
    assert set(embed.taste_scores(con, ix)) == set(ids)


def test_zotero_child_failure_does_not_duplicate(tmp_path, monkeypatch):
    from digest import db, zotero
    monkeypatch.setenv("ZOTERO_API_KEY", "k")
    monkeypatch.setenv("ZOTERO_USER_ID", "42")
    fake = FakeZotero()

    def flaky(method, url, **kw):
        if method == "POST" and url.endswith("/items") and kw["json"][0].get("parentItem"):
            raise zotero.requests.ConnectionError("network blip")         # note/PDF link fails
        return fake(method, url, **kw)
    monkeypatch.setattr(zotero.requests, "request", flaky)
    con = db.connect(str(tmp_path / "d.db"))
    db.upsert(con, [_paper("2410.00001")], "2026-10-01", {"total": 1, "cost": 0, "calls": 0})
    db.set_like(con, "2410.00001", True)
    cfg = {"zotero": {}}
    assert zotero.sync(con, cfg) == "1 added"
    assert zotero.sync(con, cfg) == "nothing to push"                     # mapped: no second item
    items = [d for d in fake.items.values() if d.get("itemType") == "preprint"]
    assert len(items) == 1 and items[0]["date"] == "2024"                 # submission year, not digest date
