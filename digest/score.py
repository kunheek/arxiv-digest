"""Stage 1: score every abstract for relevance AND quality with a cheap model."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .fetch import Paper
from .llm import LLM

SYSTEM = """You are a research assistant triaging today's arXiv papers for one researcher.

<researcher_profile>
{profile}
</researcher_profile>

<papers_they_love>
{seeds}
</papers_they_love>

For each paper you receive, rate how much THIS researcher would want to read it:
10 = must read (squarely on their core topic), 7-9 = strongly relevant,
4-6 = adjacent / possibly useful, 1-3 = tangential, 0 = irrelevant.
Be strict: most papers in a broad category should score 0-3.

Also rate the paper's QUALITY from the abstract alone, independently of the topic
(0-10, be a skeptical reviewer, median paper ≈ 5):
  8-10 = clear, significant idea with strong, specific evidence (quantitative gains over
         strong recent baselines, broad benchmarks, ablations, theory that is actually proved)
  5-7  = sound and useful but incremental, or evidence only partly described
  2-4  = vague, mostly hype, weak or unspecified evaluation, trivial tweak, or a
         claim that outruns the evidence
  0-1  = no substantive contribution visible
Judge the evidence and the idea, never the authors' or labs' prestige. Do not reward a
paper for matching the researcher's topic here; that is the relevance score's job.
Finally rate the paper's IMPACT (0-10), again independently of this researcher's topics:
would the wider computer-vision / machine-learning community see it as a big or foundational
contribution (a new foundation model or widely usable open model, a major dataset or
benchmark, a paradigm-shifting method, a result likely to be cited across subfields)?
  9-10 = landmark, rare (usually 0-2 papers a day)   7-8 = broadly important
  4-6  = notable within its subfield                 0-3  = ordinary
Do not inflate it: impact needs a visible claim of broad significance plus evidence, and
author or lab prestige alone is not evidence.
Score every paper you receive."""

SCHEMA = {
    "type": "object",
    "properties": {
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "arXiv id exactly as given",
                    },
                    "score": {
                        "type": "number",
                        "description": "relevance to the researcher, 0-10",
                    },
                    "quality": {"type": "number", "description": "paper quality, 0-10"},
                    "impact": {
                        "type": "number",
                        "description": "field-wide importance, 0-10",
                    },
                    "quality_note": {
                        "type": "string",
                        "description": "max 12 words: the main strength or weakness",
                    },
                    "reason": {"type": "string", "description": "max 15 words"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "1-3 short topic tags",
                    },
                },
                "required": [
                    "id",
                    "score",
                    "quality",
                    "impact",
                    "quality_note",
                    "reason",
                    "tags",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["papers"],
    "additionalProperties": False,
}


def build_system(cfg: dict, liked: list[str]) -> str:
    seeds = list(cfg.get("seed_papers", [])) + liked
    return SYSTEM.format(
        profile=cfg["interest_profile"].strip(),
        seeds="\n".join(f"- {s}" for s in seeds) or "(none)",
    )


def score_all(papers: list[Paper], cfg: dict, llm: LLM, liked: list[str]) -> None:
    system = build_system(cfg, liked)
    model = cfg["models"]["scorer"]
    bs = cfg["models"].get("score_batch_size", 20)
    batches = [papers[i : i + bs] for i in range(0, len(papers), bs)]
    by_id = {p.id: p for p in papers}

    def run(batch: list[Paper]) -> list[dict]:
        user = "\n\n".join(
            f'<paper id="{p.id}">\nTitle: {p.title}\nCategories: {", ".join(p.categories)}\n'
            f"Abstract: {p.abstract[:2500]}\n</paper>"
            for p in batch
        )
        try:
            out = llm.ask_structured(
                model, system, user, SCHEMA, max_tokens=160 * len(batch) + 500
            )
            return out.get("papers", [])
        except Exception as e:  # one bad batch shouldn't kill the digest
            print(f"    scoring batch failed, skipping {len(batch)} papers: {e}")
            return []

    # first batch alone warms the prompt cache, then the rest in parallel
    results = [run(batches[0])] if batches else []
    with ThreadPoolExecutor(max_workers=4) as pool:
        results += list(pool.map(run, batches[1:]))

    scored = 0
    for res in results:
        for r in res:
            p = by_id.get(str(r.get("id", "")).strip().split("v")[0])
            if p:
                p.interest = float(r.get("score", 0))
                p.quality = max(0.0, min(10.0, float(r.get("quality", 5))))
                p.impact = max(0.0, min(10.0, float(r.get("impact", 0))))
                p.quality_note = r.get("quality_note", "")
                p.reason = r.get("reason", "")
                p.tags = list(r.get("tags", []))[:3]
                scored += 1
    print(f"    scored {scored}/{len(papers)} papers")
