# arXiv daily digest

Every weekday this scans new **cs.CV, cs.CG, cs.LG and stat.ML** papers, scores each one
for **relevance to your interests and for quality** with Claude, boosts papers from
researchers and labs you watch, summarizes the best ones, and publishes them to a website, with a **Discord** ping when it's updated.
Everything is archived in a local SQLite database and published as a searchable website
on **GitHub Pages**. It runs from cron on your own Linux machine.

```
arXiv RSS (≈ 450 new papers/day)
   │
   ├─ 1. Haiku scores every abstract: relevance (0–10) and quality (0–10)
   ├─ 2. Watchlist: author names + affiliations (from arXiv HTML, top ~40 only)
   ├─ 3. Rank = relevance × quality factor + author bonus + institution bonus
   ├─ 4. Sonnet writes TL;DR + "why you should care" for the top 15 and re-judges
   │     quality after reading the introduction
   └─ 5. data/digest.db (everything) · website (relevance ≥ 2) · one Discord message
```

Expected cost: roughly **$0.15–0.50 per day**. Every run prints its actual cost.

## Setup
```bash
git clone https://github.com/kunheek/arxiv-digest.git ~/arxiv-digest && cd ~/arxiv-digest
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env && nano .env     # ANTHROPIC_API_KEY, optional DISCORD_WEBHOOK
scripts/run_daily.sh --dry-run --max-papers 20          # test: no Discord, nothing saved
scripts/install_cron.sh                                 # Mon–Fri 14:03 and 16:03 KST
```
- **API key:** <https://platform.claude.com> → API keys. The digest uses the API, which is
  billed separately from chat usage.
- **Discord (optional):** Server Settings → Integrations → Webhooks → New Webhook → copy URL.
  Each run posts one "digest updated" message with a link to the site (no papers), and
  failures are posted there too.
- **Cron** needs the machine clock to be KST (the installer checks). The 16:03 run is a
  catch-up in case arXiv's feed was late; it does nothing if 14:03 got today's papers.
  Cron doesn't catch up on runs missed while the machine was off. Each run `git pull`s first
  (set `AUTO_PULL=0` in `.env` to stop that).

| Task | Command |
|---|---|
| Run now | `scripts/run_daily.sh` |
| Show schedule / uninstall | `crontab -l` / `scripts/install_cron.sh --remove` |
| Logs | `tail -f logs/$(date +%Y-%m).log` |
| Rebuild + publish site (free) | `.venv/bin/python run.py --site-only` |
| Build only, preview locally | `run.py --site-only --no-publish && cd site && python3 -m http.server` |
| Tests | `.venv/bin/pip install pytest && .venv/bin/python -m pytest -q` |

## Website
After each run the site is built into `site/` and force-pushed as a single commit to the
`gh-pages` branch (one-time: GitHub → Settings → Pages → *Deploy from a branch* → `gh-pages`,
root). Public unless your plan supports private Pages. Controlled by `delivery.site` and
`delivery.publish`.

- **Views:** Dense (one line per paper), Compact, Full. Click a row to expand.
- **Search:** words match title, tags, authors, id. Operators: `au:` `tag:` `cat:` `date:`
  `id:` `score>=7` `quality>=7` `rating>=4` `has:code` `is:watch` `is:top` `is:rated`
  `is:unrated`, `-exclude`, `"quoted phrase"`. The *abstracts* toggle also searches TL;DRs and
  abstracts. Press `?` on the site for shortcuts. Searches live in the URL, so they're shareable.
- **Scale:** visitors load one compact index plus one small file per day they open.
- **Database:** `data/digest.db` (git-ignored, back it up) keeps every scored paper
  (`limits.db_min_interest`); the site shows those with relevance ≥ `limits.site_min_interest`.

### Ratings (feedback loop)
Rate papers 1–5 stars on the site (or press `1`–`5` on the selected row). Ratings are stored
in your browser. To teach the scorer your taste:

1. Click **Export ratings** on the site → saves `ratings.json`.
2. `.venv/bin/python run.py --import-ratings ~/Downloads/ratings.json`

Papers rated ≥ `feedback.min_rating` become positive examples in later scoring runs.

## Tuning (`config.yaml`)
- **`interest_profile`** matters most. Be specific about what's core, adjacent and unwanted.
- **`seed_papers`** are titles that are "exactly my taste".
- **`ranking.quality_weight`** (0–1): how much estimated quality matters. Final relevance is
  scaled by `1 − w + w·quality/10`, so with the default 0.3 a zero-quality paper keeps 70% of
  its relevance and a 10-quality paper keeps all of it. Quality never rescues an irrelevant
  paper. `0` ranks by relevance only. The quality score is judged from the abstract (and the
  introduction for the top 15) by an LLM, so treat it as a noisy signal: it rewards specific,
  evidenced claims and penalizes vague or hype-heavy ones, not author or lab prestige.
- **`watch_authors` / `watch_institutions`** add bonuses. Aliases use `|`. Keep them modest.
- **Cost knobs:** `summarize_top_k`, `enrich_top_n`. Switching the summarizer to
  `claude-opus-5-5` roughly doubles summary cost.

## Notes
- `state/seen.json` (local) remembers processed papers so nothing is posted twice.
- Affiliations come from the paper's arXiv HTML page. Papers without an HTML version
  still get scored; they just can't earn the institution bonus.
