"""Paper explanations: Opus reads the whole PDF (text and figures) and writes down the paper's
key information for a reader: the problem, the idea, how it works, the results, what's new, and
the limitations the authors state. It reports the paper; it doesn't review it.

  python run.py --explain 2610.31642 [more ids or arXiv links]
  or "Explain this paper" in an expanded row on the local site (run.py --serve)

Explanations live in the database (table explanations, costs in explain_costs) and are
published with the site.
"""

from __future__ import annotations

import re

import requests

from . import db
from .fetch import UA
from .llm import LLM

PDF_URL = "https://arxiv.org/pdf/{id}"
MAX_PDF_BYTES = 30 * 1024 * 1024  # the API takes at most 32 MB per request

SYSTEM = """You explain one research paper to a busy researcher who has not read it.
Your job is to convey the paper's key information accurately and clearly. Do not review,
critique or score the paper, and do not add your own opinion of it.

Write Markdown with exactly these sections:
## In short
2-3 sentences: what the paper does and its main finding.
## Problem
What problem it addresses and why it matters, as the authors motivate it.
## Key idea
The central insight in plain words.
## How it works
The method step by step. Explain the key equations or algorithms in words, then state them.
## Main results
The headline results with the paper's own numbers, datasets, metrics and baselines. Use a small
table when comparing several numbers. Mention what the key figures show.
## What's new
How it differs from prior work, as the paper positions itself.
## Limitations
Only limitations the authors state. Write "None stated." if there are none.
## Key terms
3-6 terms a reader needs, one line each.

Accuracy rules:
- Report only what the paper says. Copy numbers exactly, with units and what they are compared to.
- If something is unclear or not reported, say "not reported"; never guess or fill in.
Format rules:
- Use only ## headings, paragraphs, "-" bullets (one nesting level, indented 2 spaces), **bold**,
  `code` and simple pipe tables.
- No LaTeX: write math in plain Unicode, e.g. softmax(z)ₖ = exp(zₖ) / Σⱼ exp(zⱼ), m ≥ 16.6.
- 500-900 words."""

PROMPT = "Explain this paper."


def arxiv_id(s: str) -> str:
    """'2610.31642', 'arXiv:2610.31642v2' or an abs/pdf link → '2610.31642' (the database's
    key; the PDF fetched is the latest version)."""
    m = re.search(r"(\d{4}\.\d{4,5})(?:v\d+)?", s)
    if not m:
        raise ValueError(f"not an arXiv id or link: {s!r}")
    return m.group(1)


def fetch_pdf(pid: str) -> bytes:
    r = requests.get(PDF_URL.format(id=pid), headers=UA, timeout=60)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError(f"arXiv returned no PDF for {pid}")
    if len(r.content) > MAX_PDF_BYTES:
        raise ValueError(f"PDF is {len(r.content) >> 20} MB, over the API's 32 MB limit")
    return r.content


def explain(con, pid: str, cfg: dict, llm: LLM) -> dict:
    """Explain one paper the caller has claimed (db.claim_explanation) and store the result.
    On failure the row is marked failed (so the site stops waiting) and the error re-raised."""
    c = cfg.get("explain", {})
    model = c.get("model", "claude-opus-5-5")
    try:
        text, cost, usage = llm.ask_document(
            model, SYSTEM, fetch_pdf(pid), PROMPT, effort=c.get("effort", "high")
        )
    except Exception as e:
        db.fail_explanation(con, pid, f"{type(e).__name__}: {e}")
        raise
    db.save_explanation(con, pid, text.strip(), model, usage.input_tokens, usage.output_tokens, cost)
    return db.get_explanation(con, pid)
