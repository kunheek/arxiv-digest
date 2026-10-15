"""Stage 1: score every abstract for relevance with a cheap model."""
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
Score every paper you receive."""

SCHEMA = {
    "type": "object",
    "properties": {
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "arXiv id exactly as given"},
                    "score": {"type": "number", "description": "0-10"},
                    "reason": {"type": "string", "description": "max 15 words"},
                    "tags": {"type": "array", "items": {"type": "string"},
                             "description": "1-3 short topic tags"},
                },
                "required": ["id", "score", "reason", "tags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["papers"],
    "additionalProperties": False,
}


def build_system(cfg: dict, liked: list[str]) -> str:
    seeds = list(cfg.get("seed_papers", [])) + liked
    return SYSTEM.format(profile=cfg["interest_profile"].strip(),
                         seeds="\n".join(f"- {s}" for s in seeds) or "(none)")


def score_all(papers: list[Paper], cfg: dict, llm: LLM, liked: list[str]) -> None:
    system = build_system(cfg, liked)
    model = cfg["models"]["scorer"]
    bs = cfg["models"].get("score_batch_size", 20)
    batches = [papers[i:i + bs] for i in range(0, len(papers), bs)]
    by_id = {p.id: p for p in papers}

    def run(batch: list[Paper]) -> list[dict]:
        user = "\n\n".join(
            f"<paper id=\"{p.id}\">\nTitle: {p.title}\nCategories: {', '.join(p.categories)}\n"
            f"Abstract: {p.abstract[:2500]}\n</paper>" for p in batch)
        try:
            out = llm.ask_structured(model, system, user, SCHEMA,
                                     max_tokens=120 * len(batch) + 500)
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
                p.reason = r.get("reason", "")
                p.tags = list(r.get("tags", []))[:3]
                scored += 1
    print(f"    scored {scored}/{len(papers)} papers")
