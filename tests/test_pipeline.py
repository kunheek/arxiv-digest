"""Offline end-to-end test: fake RSS, fake arXiv HTML, fake Claude. Run: python -m pytest -q"""

import json
import os
import re
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
