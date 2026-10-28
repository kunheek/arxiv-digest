"""On-demand full-paper review: Opus reads the whole PDF (text and figures).

  python run.py --review 2610.31642 [more ids or arXiv links]

Each review goes to reviews/<id>.md and its cost is appended to reviews/costs.csv.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re

import requests

from .fetch import UA
from .llm import LLM

PDF_URL = "https://arxiv.org/pdf/{id}"
MAX_PDF_BYTES = 30 * 1024 * 1024  # the API takes at most 32 MB per request
OUT_DIR = "reviews"
COST_LOG = "costs.csv"
COLUMNS = ["date", "id", "model", "input_tokens", "output_tokens", "cost_usd"]

SYSTEM = """You are an expert ML reviewer writing a comprehensive peer review (ICLR/NeurIPS style).
Structure the review in Markdown with these sections:
1. Summary (what the paper claims and does)
2. Strengths
3. Weaknesses (be specific: missing baselines, unsupported claims, math errors, scope)
4. Correctness check (verify the key derivations/claims you can check yourself)
5. Experiments assessment
6. Questions for the authors
7. Verdict: score 1-10 with confidence 1-5 and a one-paragraph justification
Be skeptical and concrete. Judge substance, not author or lab prestige. 1200-2000 words."""

PROMPT = "Write the review of this paper."


def arxiv_id(s: str) -> str:
    """'2610.31642', 'arXiv:2610.31642v2' or an abs/pdf link → '2610.31642' / '2610.31642v2'."""
    m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", s)
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


def review(pid: str, cfg: dict, llm: LLM, out_dir: str = OUT_DIR) -> tuple[str, float]:
    """Review one paper; returns (path of the review, its cost in USD)."""
    model = cfg["models"].get("reviewer", "claude-opus-5-5")
    effort = cfg["models"].get("review_effort", "high")
    text, cost, usage = llm.ask_document(model, SYSTEM, fetch_pdf(pid), PROMPT, effort=effort)
    today = dt.date.today().isoformat()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{pid}.md")
    with open(path, "w") as f:
        f.write(
            f"> [arXiv:{pid}](https://arxiv.org/abs/{pid}) · reviewed by {model} on {today} · "
            f"{usage.input_tokens:,} in / {usage.output_tokens:,} out tokens · ${cost:.3f}\n\n"
        )
        f.write(text.strip() + "\n")
    log = os.path.join(out_dir, COST_LOG)
    new = not os.path.exists(log)
    with open(log, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLUMNS)
        w.writerow([today, pid, model, usage.input_tokens, usage.output_tokens, f"{cost:.4f}"])
    return path, cost


def total_cost(out_dir: str = OUT_DIR) -> tuple[int, float]:
    """(number of reviews, total USD) from the cost log."""
    try:
        with open(os.path.join(out_dir, COST_LOG)) as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return 0, 0.0
    return len(rows), sum(float(r["cost_usd"]) for r in rows)
