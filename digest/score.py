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
Finally rate the paper's IMPACT (0-10).
{impact_rubric}
Score every paper you receive."""

# shared with the impact review below and the summarizer, so all three judge impact alike
IMPACT_RUBRIC = """IMPACT is independent of this researcher's own subfield: would a broad computer-vision /
graphics / deep-learning researcher put this paper on their reading list even though it is
outside their subfield? Papers that earn it:
  - general-purpose building blocks many models could adopt: optimizers, normalization, losses,
    architectures, attention and positional encodings, training or sampling recipes
    (think Adam, the Transformer, BatchNorm, ResNet, LoRA)
  - generative-modeling frameworks that change how a field works (think DDPM, flow matching, NeRF)
  - theory that explains or fixes practice everyone uses (why an optimizer, normalization
    or attention behaves as it does)
  - widely usable foundation models, datasets or benchmarks
They can come from any field, NLP included. A simple, general idea with clear evidence counts
even when the abstract is modest: Adam's made no grand claims.
  9-10 = could become standard practice or a landmark, rare (usually 0-2 papers a day)
  7-8  = a broad CV / graphics / DL reader should know about it
  4-6  = notable within its own subfield              0-3 = ordinary
Narrow applications, incremental subfield gains and benchmark-chasing score low however strong
the numbers. Author or lab prestige is not evidence.

<papers_they_value_for_field_wide_importance>
{broad}
</papers_they_value_for_field_wide_importance>"""


def impact_rubric(cfg: dict) -> str:
    broad = cfg.get("broad_examples") or []
    return IMPACT_RUBRIC.format(broad="\n".join(f"- {b}" for b in broad) or "(none)")


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
        impact_rubric=impact_rubric(cfg),
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


REVIEW_SYSTEM = """You are a senior computer-vision / graphics / deep-learning researcher giving a second
opinion on the field-wide IMPACT of today's arXiv papers. A cheaper model rated them first;
judge each one afresh from its title and abstract (0-10).

{impact_rubric}"""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "arXiv id exactly as given"},
                    "impact": {"type": "number", "description": "field-wide importance, 0-10"},
                },
                "required": ["id", "impact"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["papers"],
    "additionalProperties": False,
}


def review_impact(papers: list[Paper], cfg: dict, llm: LLM) -> list[Paper]:
    """The summarizer re-judges impact for the top `ranking.impact_review_n` papers by impact,
    so a general idea the cheap scorer underrated (an Adam, an attention) can still reach the
    impact floor. Returns the papers whose impact changed."""
    n = cfg["ranking"].get("impact_review_n", 20)
    cands = sorted(papers, key=lambda p: (p.impact, p.quality), reverse=True)[:n]
    if not cands:
        return []
    user = "\n\n".join(
        f'<paper id="{p.id}">\nTitle: {p.title}\nCategories: {", ".join(p.categories)}\n'
        f"Abstract: {p.abstract[:2500]}\n</paper>"
        for p in cands
    )
    system = REVIEW_SYSTEM.format(impact_rubric=impact_rubric(cfg))
    try:
        out = llm.ask_structured(
            cfg["models"]["summarizer"], system, user, REVIEW_SCHEMA,
            max_tokens=40 * len(cands) + 300,
        )
    except Exception as e:  # keep the scorer's impact if the review fails
        print(f"    impact review failed, keeping the scorer's ratings: {e}")
        return []
    by_id = {p.id: p for p in cands}
    changed = []
    for r in out.get("papers", []):
        p = by_id.get(str(r.get("id", "")).strip().split("v")[0])
        if p:
            new = max(0.0, min(10.0, float(r.get("impact", p.impact))))
            if new != p.impact:
                p.impact = new
                changed.append(p)
    print(f"    re-judged impact for {len(cands)} papers, {len(changed)} changed")
    return changed
