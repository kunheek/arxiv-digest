"""Daily arXiv digest.

    python run.py                 # full run: fetch → score → enrich → summarize → deliver
    python run.py --dry-run       # no Discord, no DB/state update; writes digest.md
    python run.py --max-papers 60 # cap papers scored (cheap test)

  Free modes (no arXiv, no Claude):
    python run.py --site-only [--no-publish]   # rebuild (and publish) the website from the DB
    python run.py --import-ratings FILE        # load ratings.json exported from the website
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import yaml

from digest import db, deliver, publish, site, state
from digest.env import load_env
from digest.enrich import enrich, final_score
from digest.fetch import fetch_today
from digest.llm import LLM
from digest.score import score_all
from digest.summarize import summarize


def build_site(con, cfg: dict, publish_it: bool = True) -> None:
    d = cfg["delivery"]
    n = site.build(con, "site", d.get("site_title", "arXiv digest"),
                   cfg["limits"].get("site_min_interest", 3))
    print(f"    built site/ with {n} papers")
    if publish_it and d.get("publish"):
        try:
            publish.publish("site", d.get("publish_branch", "gh-pages"))
            print("    published to GitHub Pages")
        except Exception as e:      # never fail the digest because of the website
            print(f"    WARNING: publish failed: {getattr(e, 'stderr', None) or e}")


def main(argv=None, llm: LLM | None = None, fetcher=fetch_today) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--state", default="state/seen.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-papers", type=int, default=0)
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--site-only", action="store_true",
                    help="rebuild the website from the database (and publish it); free")
    ap.add_argument("--no-publish", action="store_true",
                    help="with --site-only: build site/ but don't push to GitHub Pages")
    ap.add_argument("--import-ratings", metavar="FILE",
                    help="load ratings exported from the website into the database")
    args = ap.parse_args(argv)

    load_env(os.path.join(os.path.dirname(os.path.abspath(args.config)), ".env"))
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()

    if args.import_ratings or args.site_only:
        con = db.connect(args.db)
        if args.import_ratings:
            print(f"imported {db.import_ratings(con, args.import_ratings)} ratings into {args.db}")
        if args.site_only:
            build_site(con, cfg, publish_it=not args.no_publish)
        return 0

    if llm is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            print("ERROR: ANTHROPIC_API_KEY is not set (or is empty).\n"
                  "  Put  ANTHROPIC_API_KEY=sk-ant-...  in .env (see .env.example).")
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

    use_discord = cfg["delivery"].get("discord") and not args.dry_run and os.environ.get("DISCORD_WEBHOOK")
    con = db.connect(args.db)
    liked = []
    fb = cfg.get("feedback", {})
    if fb.get("enabled"):
        liked = db.liked_titles(con, fb.get("min_rating", 4), fb.get("max_examples", 30))
        if liked:
            print(f"    using {len(liked)} papers you rated highly as examples")

    print("2/5 scoring abstracts")
    score_all(papers, cfg, llm, liked)

    print("3/5 watchlists + affiliations")
    enrich(papers, cfg)

    ranked = sorted(papers, key=lambda p: (p.final, p.interest), reverse=True)
    k = cfg["limits"]["summarize_top_k"]
    top = ranked[:k]
    # tolerate scorer noise: also summarize the most relevant papers that a low quality
    # score pushed out of the top k, so one bad judgement can't hide a likely must-read
    rescued = sorted((p for p in ranked[k:] if p.interest >= cfg["limits"].get("rescue_min_interest", 7)),
                     key=lambda p: p.interest, reverse=True)[: cfg["limits"].get("rescue_k", 5)]
    print(f"4/5 summarizing top {len(top)}" + (f" + {len(rescued)} rescued by relevance" if rescued else ""))
    summarize(top + rescued, cfg, llm)
    for p in top + rescued:            # the summarizer re-judged quality after reading the intro
        p.final = final_score(p, cfg)
    ranked.sort(key=lambda p: (p.final, p.interest), reverse=True)
    top = ranked[:k]

    stats = {"total": len(papers), "cost": llm.cost, "calls": llm.calls}
    print(f"5/5 delivering  (≈ ${llm.cost:.3f}, {llm.calls} calls)")
    deliver.to_markdown(top, today, stats, "digest.md")
    if use_discord:
        deliver.to_discord(top[: cfg["limits"]["discord_top_k"]], today, stats,
                           os.environ["DISCORD_WEBHOOK"])
        print("    posted to Discord")

    if not args.dry_run:
        db.upsert(con, [p for p in ranked if p.interest >= cfg["limits"].get("db_min_interest", 0)],
                  today, stats)
        if cfg["delivery"].get("site"):
            build_site(con, cfg)
        seen.update({p.id: today for p in papers})
        state.save(args.state, seen, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
