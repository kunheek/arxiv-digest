# arXiv daily digest

Every weekday this scans new **cs.CV, cs.CG, cs.LG and stat.ML** papers, scores each one
for **relevance to your interests, quality, and field-wide impact** with Claude, boosts papers from
researchers and labs you watch, summarizes the best ones, and publishes them to a website, with a **Discord** ping when it's updated.
Everything is archived in a local SQLite database and published as a searchable website
on **GitHub Pages**. It runs from cron on your own Linux machine.

```
arXiv RSS (≈ 450 new papers/day)
   │
   ├─ 1. Haiku scores every abstract: relevance, quality, and impact (each 0–10)
   ├─ 2. Watchlist: author names + affiliations (from arXiv HTML, top ~40 only)
   ├─ 3. Rank = relevance × quality factor + author/institution bonus,
   │     floored by impact so big / foundational papers survive any topic
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
  Each run posts one "digest updated" message with a link to the site (no papers). If the feed
  is empty (e.g. a US holiday) you get a "no new papers" notice instead, and
  failures are posted there too.
- **Cron** runs in the system timezone (not your shell's `TZ`); the installer converts
  14:03/16:03 KST to it (check with `timedatectl`). The 16:03 run is a
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
  `id:` `score>=7` `quality>=7` `impact>=7` `rating>=4` `has:code` `is:watch` `is:top` `is:landmark` `is:rated`
  `is:unrated`, `-exclude`, `"quoted phrase"`. The *abstracts* toggle also searches TL;DRs and
  abstracts. Press `?` on the site for shortcuts. Searches live in the URL, so they're shareable.
- **Scale:** visitors load one compact index plus one small file per day they open.
- **Database:** `data/digest.db` (git-ignored, back it up) keeps every scored paper
  (`limits.db_min_interest`). **FIFO limit:** when it holds more than `limits.db_max_papers`
  (default 300,000 ≈ 2.5 years, ~650 MB), the oldest papers are deleted. Papers you rated or bookmarked
  are never deleted. The database never leaves your machine (git-ignored; only the generated
  `site/` files are pushed). The public site is a separate, smaller view: papers with relevance
  ≥ `limits.site_min_interest` (or high impact), newest `limits.site_max_papers` (default
  6,000 ≈ 65 days), so GitHub Pages stays small while the local DB and local site keep the
  full history.

### Local site (all papers)
```bash
.venv/bin/python run.py --serve                  # http://127.0.0.1:8000
.venv/bin/python run.py --serve --host 0.0.0.0   # reachable from your LAN (no auth!)
```
Same UI, but it serves **every** paper in the database straight from SQLite (always current,
no rebuild after a run). Ratings and bookmarks you set there are saved directly to the
database, so no export is needed. Keep it running with `tmux`, or as a systemd user service.

### Ratings and bookmarks
Rate papers 1–5 stars (press `1`–`5` on the selected row) and bookmark them with 🔖 (`b`).
Like with ♥ (`l`). Liked, bookmarked and rated papers are protected from FIFO deletion. Search with `is:saved`,
`is:liked`, `is:rated`, `rating>=4`, or the 🔖 saved checkbox. Likes (and ratings ≥ `feedback.min_rating`) become positive examples for scoring.

- **Only the local site can change them**; they're saved straight into the database.
- **GitHub Pages is read-only.** Each publish includes `data/prefs.json` (your ratings and
  bookmarks) so the public site shows the same picks, and rated/bookmarked papers are always
  included even beyond `site_max_papers`. To push new picks right away instead of waiting for the
  next run: `.venv/bin/python run.py --site-only`.

Papers rated ≥ `feedback.min_rating` become positive examples in later scoring runs.

### Embeddings (local only)
Every paper is embedded on your CPU with `fastembed` (`BAAI/bge-small-en-v1.5`, 384-d, stored as
float16 in the `vectors` table, never published). The local site uses them for:
- **Show similar papers** in an expanded row, and a **"for you ♥"** sort with a ♥0–10 taste badge:
  closeness to what you liked / bookmarked / rated ≥ 4 (minus what you rated ≤ 2).
  Needs at least 3 picks.
- New papers are embedded at the end of each daily run. Backfill the whole DB once with
  `.venv/bin/python run.py --embed-backfill` (≈1.5 min per 2,000 papers).
- `.venv/bin/python run.py --eval-taste` tests, on your own picks, whether the taste score ranks
  them above other papers better than Sonnet's score does (needs ≥ 8 picks).

The taste score is not mixed into the daily ranking: Sonnet still decides what gets summarized.

### Zotero
Papers you **like** or **bookmark** on the local site are pushed to your Zotero library: a
`preprint` item in the `arxiv-digest` collection, with your digest tags, a child note (TL;DR,
why-you, scores) and a link to the arXiv PDF (no Zotero storage used). Ratings are not pushed.
A paper already in your library is filed into the collection instead of duplicated, and
un-liking never deletes anything from Zotero.

1. Create a key with **write access** and **notes access** at https://www.zotero.org/settings/keys and add
   `ZOTERO_API_KEY=...` to `.env` (`ZOTERO_USER_ID` is optional; it's looked up from the key).
2. `.venv/bin/python run.py --zotero-sync` checks the key and pushes everything you've already
   liked or bookmarked.

From then on each like/bookmark is pushed in the background (the running server picks up the key
from `.env` without a restart), and the daily run retries anything that failed.
Settings: `zotero:` in `config.yaml`.

### BibTeX
**Copy BibTeX** in an expanded row (both sites) gives the same `@misc` entry as
`arxiv.org/bibtex/<id>`, built locally. `.venv/bin/python run.py --export-bib refs.bib` writes
all liked/bookmarked papers. For a `.bib` that stays in sync with your library, use Zotero's
Better BibTeX plugin on the `arxiv-digest` collection.

## Tuning (`config.yaml`)
- **`interest_profile`** matters most. Be specific about what's core, adjacent and unwanted.
- **`seed_papers`** are titles that are "exactly my taste".
- **`ranking.quality_weight`** (0–1): how much estimated quality matters. Final relevance is
  scaled by `1 − w + w·quality/10`, so with the default 0.3 a zero-quality paper keeps 70% of
  its relevance and a 10-quality paper keeps all of it. Quality never rescues an irrelevant
  paper. `0` ranks by relevance only. The quality score is judged from the abstract (and the
  introduction for the top 15) by an LLM, so treat it as a noisy signal: it rewards specific,
  evidenced claims and penalizes vague or hype-heavy ones, not author or lab prestige.
- **`ranking.impact_min` / `impact_weight`**: big or foundational papers are kept *regardless
  of your interests*. The scorer rates each paper's field-wide impact (new foundation model,
  major dataset or benchmark, paradigm shift; prestige alone doesn't count). At
  `impact_min` (7) or above, a paper's final score is at least `impact_weight` × impact, so a
  9 lands at 8.1 among the top picks, gets a summary, and shows on the site with a 📌 even if
  it's far from your topics. Papers from `impact_min − 1` also appear on the site. Raise
  `impact_min` if too many ordinary papers get flagged, or set it to 11 to turn this off.
- **`watch_authors` / `watch_institutions`** add bonuses. Aliases use `|`. Keep them modest.
- **Cost knobs:** `summarize_top_k`, `enrich_top_n`. Switching the summarizer to
  `claude-opus-5-5` roughly doubles summary cost.

## Notes
- `state/seen.json` (local) remembers processed papers so nothing is posted twice.
- Affiliations come from the paper's arXiv HTML page. Papers without an HTML version
  still get scored; they just can't earn the institution bonus.
