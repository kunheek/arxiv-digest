"""Daily arXiv digest.

    python run.py                 # full run: fetch → score → enrich → summarize → deliver
    python run.py --dry-run       # no Discord/Sheets, no state update; writes digest.md
    python run.py --max-papers 60 # cap papers scored (cheap test)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import yaml

from digest import deliver, state
from digest.enrich import enrich
from digest.fetch import fetch_today
from digest.llm import LLM
from digest.score import score_all
from digest.summarize import summarize


def main(argv=None, llm: LLM | None = None, fetcher=fetch_today) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--state", default="state/seen.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-papers", type=int, default=0)
    args = ap.parse_args(argv)

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    if llm is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            print("ERROR: ANTHROPIC_API_KEY is not set (or is empty).\n"
                  "  • Mac: run  export ANTHROPIC_API_KEY=sk-ant-...  in the SAME terminal, then retry.\n"
                  "  • GitHub: Settings → Secrets and variables → Actions → Repository secrets,\n"
                  "    name exactly ANTHROPIC_API_KEY, value = just the key.")
            return 1
        if not key.startswith("sk-ant-"):
            print("WARNING: ANTHROPIC_API_KEY doesn't start with 'sk-ant-'. Check you pasted only the key.")
        llm = LLM()

    print("1/5 fetching arXiv feeds")
    papers = fetcher(cfg["categories"], cfg["announce_types"])
    seen = state.load(args.state)
    papers = [p for p in papers if p.id not in seen]
    if args.max_papers:
        papers = papers[: args.max_papers]
    print(f"    {len(papers)} new papers to process")
    if not papers:
        print("nothing new today (weekend/holiday, or already processed)")
        return 0

    use_sheets = cfg["delivery"].get("sheets") and not args.dry_run and os.environ.get("GOOGLE_CREDS")
    use_discord = cfg["delivery"].get("discord") and not args.dry_run and os.environ.get("DISCORD_WEBHOOK")
    liked = deliver.liked_from_sheets(cfg) if use_sheets else []
    if liked:
        print(f"    using {len(liked)} highly-rated papers from your Sheet as examples")

    print("2/5 scoring abstracts")
    score_all(papers, cfg, llm, liked)

    print("3/5 watchlists + affiliations")
    enrich(papers, cfg)

    ranked = sorted(papers, key=lambda p: (p.final, p.interest), reverse=True)
    top = ranked[: cfg["limits"]["summarize_top_k"]]
    print(f"4/5 summarizing top {len(top)}")
    summarize(top, cfg, llm)

    stats = {"total": len(papers), "cost": llm.cost, "calls": llm.calls}
    print(f"5/5 delivering  (≈ ${llm.cost:.3f}, {llm.calls} calls)")
    deliver.to_markdown(top, today, stats, "digest.md")
    if use_discord:
        deliver.to_discord(top[: cfg["limits"]["discord_top_k"]], today, stats,
                           os.environ["DISCORD_WEBHOOK"])
        print("    posted to Discord")
    if use_sheets:
        keep = [p for p in ranked if p.interest >= cfg["limits"]["sheets_min_interest"]]
        deliver.to_sheets(keep, today, cfg)
        print(f"    wrote {len(keep)} rows to Google Sheets")

    if not args.dry_run:
        seen.update({p.id: today for p in papers})
        state.save(args.state, seen, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
