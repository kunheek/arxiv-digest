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
    ("2610.00001", "Caching Attention for Fast Video DiT Inference", "Andrea Vedaldi, Jane Doe", "new",
     "We cache attention maps across denoising steps in video diffusion transformers. Code: https://github.com/x/fastdit."),
    ("2610.00002", "Feed-forward 4D Reconstruction from Monocular Video", "John Smith, Kim Lee", "cross",
     "A VGGT-style transformer that regresses dynamic pointmaps."),
    ("2610.00003", "Federated Learning for Medical Imaging", "A B, C D", "new",
     "We study federated learning for MRI segmentation."),
    ("2610.00004", "Replaced Old Paper", "X Y", "replace", "old"),
]


def make_rss():
    return RSS.format(items="\n".join(
        ITEM.format(id=i, title=t, authors=a, atype=ty, abstract=ab) for i, t, a, ty, ab in PAPERS))


class FakeClient:
    """Mimics anthropic.Anthropic().messages.create."""
    def __init__(self):
        self.messages = self
        self.calls = []

    def create(self, model, max_tokens, system, messages, output_config=None):
        schema = output_config["format"]["schema"]
        assert output_config["format"]["type"] == "json_schema"
        assert "minimum" not in json.dumps(schema)            # unsupported by the API
        assert schema.get("additionalProperties") is False    # required by the API
        self.calls.append(model)
        content = messages[0]["content"]
        if "<paper id" in content:  # scoring
            ids = re.findall(r'<paper id="([^"]+)"', content)
            score = {"2610.00001": 9, "2610.00002": 8, "2610.00003": 1}
            # a reason with quotes in it -- the old text-parsing version broke on this
            data = {"papers": [{"id": i, "score": score.get(i, 0),
                                "reason": 'uses "TeaCache"-style caching', "tags": ["video-dit"]}
                               for i in ids]}
        else:  # summary
            data = {"tldr": "Does a thing fast.", "why": "Matches your DiT work."}
        usage = types.SimpleNamespace(input_tokens=1000, output_tokens=100,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return types.SimpleNamespace(stop_reason="end_turn", usage=usage,
                                     content=[types.SimpleNamespace(type="text", text=json.dumps(data))])


def test_parse_rss():
    ps = parse_rss(make_rss())
    assert [p.id for p in ps] == ["2610.00001", "2610.00002", "2610.00003", "2610.00004"]
    assert ps[0].authors == ["Andrea Vedaldi", "Jane Doe"]
    assert ps[1].announce_type == "cross"
    assert ps[0].abstract.startswith("We cache")


def test_institution_matching():
    from digest.fetch import Paper
    p = Paper("1", "t", "a", [], [], "new", "l", affiliations=["Work submitted while at Stanford University"])
    enrich_mod.match_institutions(p, ["Massachusetts Institute of Technology|MIT", "Stanford"])
    assert p.inst_hits == ["Stanford"]  # 'submitted' must not trigger MIT


def test_end_to_end(tmp_path, monkeypatch):
    def fake_html(p, max_intro_chars=6000):
        p.affiliations = ["University of Oxford"] if p.id == "2610.00001" else ["Some Lab"]
        p.intro = "Introduction text."
    monkeypatch.setattr(enrich_mod, "fetch_html", fake_html)
    monkeypatch.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def fetcher(cats, types_):
        return [p for p in parse_rss(make_rss()) if p.announce_type in types_]

    client = FakeClient()
    llm = LLM(client)
    state = tmp_path / "seen.json"
    md = tmp_path / "digest.md"
    monkeypatch.setattr(run.deliver, "to_markdown",
                        lambda top, d, s, path, f=run.deliver.to_markdown: f(top, d, s, str(md)))
    # test uses its own watchlists so editing your real config.yaml never breaks it
    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    cfg["watch_authors"] = ["Andrea Vedaldi"]
    cfg["watch_institutions"] = ["University of Oxford|Oxford"]
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    args = ["--state", str(state), "--config", str(cfg_path)]
    assert run.main(args, llm=llm, fetcher=fetcher) == 0

    out = md.read_text()
    first = out.split("## 2.")[0]
    assert "Caching Attention" in first                  # 9 + author bonus + inst bonus
    assert "👤 Andrea Vedaldi" in out and "🏛 University of Oxford" in out
    assert "github.com/x/fastdit" in out
    assert "Replaced Old Paper" not in out               # 'replace' filtered out
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
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")))
    papers = parse_rss(make_rss())[:3]

    class Flaky:
        cost = 0; calls = 0
        def ask_structured(self, *a, **k):
            raise RuntimeError("boom")
    score_all(papers, cfg, Flaky(), [])   # must not raise
    assert all(p.interest == 0 for p in papers)
