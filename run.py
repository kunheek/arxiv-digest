"""Daily arXiv digest.

  python run.py                 # full run: fetch → score → enrich → summarize → deliver
  python run.py --dry-run       # no Discord/site, no DB/state update; writes digest.md
  python run.py --max-papers 60 # cap papers scored (cheap test)

Free modes (no arXiv, no Claude):
  python run.py --site-only [--no-publish]   # rebuild (and publish) the website from the DB
  python run.py --rescore 2026-10-16         # re-run scoring for a day already in the DB
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


def build_site(con, cfg: dict, publish_it: bool = True) -> bool:
    """Build site/ and publish it. Returns True if it was published."""
    d = cfg["delivery"]
    n = site.build(
        con,
        "site",
        d.get("site_title", "arXiv digest"),
        cfg["limits"].get("site_min_interest", 2),
        cfg["ranking"].get("impact_min", 7) - 1,
    )
    print(f"    built site/ with {n} papers")
    if publish_it and d.get("publish"):
        try:
            publish.publish("site", d.get("publish_branch", "gh-pages"))
            print("    published to GitHub Pages")
            return True
        except Exception as e:  # never fail the digest because of the website
            print(f"    WARNING: publish failed: {getattr(e, 'stderr', None) or e}")
    return False


def main(argv=None, llm: LLM | None = None, fetcher=fetch_today) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--state", default="state/seen.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-papers", type=int, default=0)
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument(
        "--site-only",
        action="store_true",
        help="rebuild the website from the database (and publish it); free",
    )
    ap.add_argument(
        "--no-publish",
        action="store_true",
        help="with --site-only: build site/ but don't push to GitHub Pages",
    )
    ap.add_argument(
        "--serve",
        action="store_true",
        help="serve ALL papers in the database on a local web server",
    )
    ap.add_argument("--host", default="127.0.0.1", help="with --serve (0.0.0.0 = whole LAN)")
    ap.add_argument("--port", type=int, default=8000, help="with --serve")
    ap.add_argument(
        "--rescore",
        metavar="DATE",
        help="re-run scoring/summaries/delivery for a day already in the database "
        "(YYYY-MM-DD), e.g. after changing the scoring rules; no arXiv fetch",
    )
    ap.add_argument(
        "--import-ratings",
        metavar="FILE",
        help="load ratings exported from the website into the database",
    )
    args = ap.parse_args(argv)

    load_env(os.path.join(os.path.dirname(os.path.abspath(args.config)), ".env"))
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()

    if args.serve:
        from digest import serve

        serve.serve(args.db, args.host, args.port, cfg["delivery"].get("site_title", "arXiv digest"))
        return 0
    if args.import_ratings or args.site_only:
        con = db.connect(args.db)
        if args.import_ratings:
            print(
                f"imported {db.import_ratings(con, args.import_ratings)} ratings into {args.db}"
            )
        if args.site_only:
            build_site(con, cfg, publish_it=not args.no_publish)
        return 0

    if llm is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            print(
                "ERROR: ANTHROPIC_API_KEY is not set (or is empty).\n"
                "  Put  ANTHROPIC_API_KEY=sk-ant-...  in .env (see .env.example)."
            )
            return 1
        if not key.startswith("sk-ant-"):
            print(
                "WARNING: ANTHROPIC_API_KEY doesn't start with 'sk-ant-'. Check you pasted only the key."
            )
        llm = LLM()

    seen = state.load(args.state)
    if args.rescore:
        today = args.rescore
        print(f"1/5 loading {today} from {args.db} (no arXiv fetch)")
        papers = db.load_day(db.connect(args.db), today)
    else:
        print("1/5 fetching arXiv feeds")
        papers = fetcher(cfg["categories"], cfg["announce_types"])
        feed_empty = not papers
        papers = [p for p in papers if p.id not in seen]
    if args.max_papers:
        papers = papers[: args.max_papers]
    print(f"    {len(papers)} new papers to process")
    if not papers:
        print("nothing new today (weekend/holiday, or already processed)")
        # tell me when the feed itself was empty (e.g. US holiday). An already-processed day
        # (the 16:03 catch-up after a good 14:03 run) stays quiet.
        hook = os.environ.get("DISCORD_WEBHOOK")
        if not args.rescore and feed_empty and not args.dry_run and hook and cfg["delivery"].get("discord"):
            hour = dt.datetime.now(ZoneInfo("Asia/Seoul")).hour
            try:
                deliver.to_discord_empty(today, hook, "16:03 KST" if hour < 15 else "")
                print("    posted 'no papers' notice to Discord")
            except Exception as e:
                print(f"    WARNING: Discord notice failed: {e}")
        return 0

    use_discord = (
        cfg["delivery"].get("discord")
        and not args.dry_run
        and os.environ.get("DISCORD_WEBHOOK")
    )
    con = db.connect(args.db)
    liked = []
    fb = cfg.get("feedback", {})
    if fb.get("enabled"):
        liked = db.liked_titles(
            con, fb.get("min_rating", 4), fb.get("max_examples", 30)
        )
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
    rescued = sorted(
        (
            p
            for p in ranked[k:]
            if p.interest >= cfg["limits"].get("rescue_min_interest", 7)
            or p.impact >= cfg["ranking"].get("impact_min", 7) + 1
        ),
        key=lambda p: max(p.interest, p.impact),
        reverse=True,
    )[: cfg["limits"].get("rescue_k", 5)]
    print(
        f"4/5 summarizing top {len(top)}"
        + (f" + {len(rescued)} rescued by relevance" if rescued else "")
    )
    summarize(top + rescued, cfg, llm)
    for p in top + rescued:  # the summarizer re-judged quality after reading the intro
        p.final = final_score(p, cfg)
    ranked.sort(key=lambda p: (p.final, p.interest), reverse=True)
    top = ranked[:k]

    stats = {"total": len(papers), "cost": llm.cost, "calls": llm.calls}
    print(f"5/5 delivering  (≈ ${llm.cost:.3f}, {llm.calls} calls)")
    deliver.to_markdown(top, today, stats, "digest.md")
    if not args.dry_run:
        db.upsert(
            con,
            [
                p
                for p in ranked
                if p.interest >= cfg["limits"].get("db_min_interest", 0)
            ],
            today,
            stats,
        )
        pruned = db.prune(con, cfg["limits"].get("db_max_papers", 0))
        if pruned:
            print(f"    pruned {pruned} oldest papers from the database (db_max_papers)")
        published = cfg["delivery"].get("site") and build_site(con, cfg)
        if use_discord:
            stats["on_site"] = sum(
                p.interest >= cfg["limits"].get("site_min_interest", 2)
                or p.impact >= cfg["ranking"].get("impact_min", 7) - 1
                for p in papers
            )
            stats["top"] = sum(p.final >= 7 for p in papers)
            deliver.to_discord(
                today,
                stats,
                os.environ["DISCORD_WEBHOOK"],
                publish.site_url(cfg) + f"#/?d={today}" if published else "",
            )
            print("    posted to Discord")
        seen.update({p.id: today for p in papers})
        state.save(args.state, seen, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
