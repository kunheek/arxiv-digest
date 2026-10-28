"""Stage 3: short, personalised summaries of the final top-K."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .fetch import Paper
from .llm import LLM
from .score import impact_rubric

SYSTEM = """You write a daily paper digest for one researcher.

<researcher_profile>
{profile}
</researcher_profile>

Given a paper's abstract and introduction, return:
- tldr: what they do and the key result, max 35 words, concrete, no hype
- why: why THIS researcher should care / how it connects to their work, max 25 words
- quality: 0-10 verdict now that you have read the introduction, as a skeptical reviewer
  (median paper ≈ 5): is the idea significant, is the evidence specific and convincing
  (strong baselines, ablations, real benchmarks), do the claims match the evidence?
  Judge substance only, not author or lab prestige, and not topic relevance.
- quality_note: max 12 words: the main strength or weakness
- impact: 0-10, as defined below.

{impact_rubric}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string"},
        "why": {"type": "string"},
        "quality": {"type": "number"},
        "quality_note": {"type": "string"},
        "impact": {"type": "number"},
    },
    "required": ["tldr", "why", "quality", "quality_note", "impact"],
    "additionalProperties": False,
}


def summarize(papers: list[Paper], cfg: dict, llm: LLM) -> None:
    system = SYSTEM.format(
        profile=cfg["interest_profile"].strip(), impact_rubric=impact_rubric(cfg)
    )
    model = cfg["models"]["summarizer"]

    def run(p: Paper):
        user = (
            f"Title: {p.title}\nAuthors: {', '.join(p.authors[:15])}\n\n"
            f"Abstract: {p.abstract}\n\nIntroduction (truncated): {p.intro or '(not available)'}"
        )
        try:
            out = llm.ask_structured(model, system, user, SCHEMA, max_tokens=600)
            p.tldr, p.why = out.get("tldr", ""), out.get("why", "")
            if "quality" in out:  # better informed than the abstract-only score
                p.quality = max(0.0, min(10.0, float(out["quality"])))
                p.quality_note = out.get("quality_note", p.quality_note)
            if "impact" in out:
                p.impact = max(0.0, min(10.0, float(out["impact"])))
        except Exception as e:  # one failed summary shouldn't kill the digest
            print(f"    summary failed for {p.id}: {e}")
            p.tldr = p.abstract[:250] + "…"

    if papers:
        run(papers[0])  # warm cache
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, papers[1:]))
