# arXiv daily digest

Every weekday this scans new **cs.CV, cs.GR, cs.CG, cs.LG and stat.ML** papers, scores each one
for **relevance to your interests, quality, and field-wide impact** with Claude, boosts papers from
researchers and labs you watch, summarizes the best ones, and publishes them to a website, with a **Discord** ping when it's updated.
Everything is archived in a local SQLite database and published as a searchable website
on **GitHub Pages**. It runs on your own machine, Linux (systemd timer or cron) or macOS (launchd).

```
arXiv RSS (≈ 450 new papers/day)
   │
   ├─ 1. Haiku scores every abstract: relevance, quality, and impact (each 0–10);
   │     Sonnet re-judges impact for the top 20 by impact
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
scripts/install.sh --serve                              # Mon–Fri 14:03 and 16:03 KST + local site
```
- **API key:** <https://platform.claude.com> → API keys. The digest uses the API, which is
  billed separately from chat usage.
- **Discord (optional):** Server Settings → Integrations → Webhooks → New Webhook → copy URL.
  Each run posts one "digest updated" message with a link to the site (no papers). If the feed
  is empty (e.g. a US holiday) you get a "no new papers" notice instead, and
  failures are posted there too.
- **`install.sh`** picks the scheduler for the machine: launchd on macOS
  (`install_launchd.sh`), a systemd user timer on Linux (`install_systemd.sh`), cron where there
  is no systemd (`install_cron.sh`, no `--serve`). You can also run any of them directly; each
  takes `--remove` to uninstall, `--config FILE` to run with another config, and (with
  `--serve`) `--host ADDR` / `--port N` for the site.
- **Linux (systemd):** the timer is `Persistent`, so a run that was due while the machine was
  off or asleep starts when it's back. The installer enables linger so the jobs run while you're
  logged out (if that's refused, run `sudo loginctl enable-linger $USER`). Next runs:
  `systemctl --user list-timers`; site log: `journalctl --user -u arxiv-digest-serve -f`.
- **Cron** runs in the system timezone (not your shell's `TZ`); the installer converts
  14:03/16:03 KST to it (check with `timedatectl`). The 16:03 run is a
  catch-up in case arXiv's feed was late; it does nothing if 14:03 got today's papers.
  Cron doesn't catch up on runs missed while the machine was off. Each run `git pull`s first
  (set `AUTO_PULL=0` in `.env` to stop that).
- **macOS:** `install_launchd.sh` uses launchd rather than cron, because launchd starts a run
  that was due while the Mac slept as soon as it wakes (one due while it was shut down is still
  missed). `--serve` also keeps the local site on http://127.0.0.1:8000, started at login. Keep
  the repo outside `~/Desktop`, `~/Documents` and `~/Downloads`: macOS blocks background jobs
  from reading those. Uninstall with `scripts/install_launchd.sh --remove`.

| Task | Command |
|---|---|
| Run now | `scripts/run_daily.sh` |
| Show schedule | `systemctl --user list-timers` (Linux) · `launchctl list \| grep arxiv` (macOS) · `crontab -l` (cron) |
| Uninstall | `scripts/install.sh --remove` |
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
database, so no export is needed. `scripts/install.sh --serve` keeps it running (systemd user
service on Linux, launchd agent on macOS); without systemd, use `tmux`.

### A shared lab server
A second digest (for example a lab's) runs from its own clone on another machine, with its own
config, `.env` and database, and is only served, never published. `config.kcvl.yaml` is one
(`delivery.publish: false`, `site: false`):
```bash
scripts/install.sh --serve --config config.kcvl.yaml --host 0.0.0.0   # http://<machine>:8000
```
- The site has **no login**: anyone who can reach the port can read it and change ratings, and
  everyone's likes and ratings train the scorer (shared taste). Keep it on the lab network or
  behind a VPN / SSH tunnel.
- **Many people at once:** the server answers each request in its own thread, and the database
  runs in SQLite's WAL mode, so clicks never wait on readers or on a daily run. Browsers keep the
  paper index and only re-download it after a run (ETag). Picks are shared: one rating per paper,
  the last click wins.
- Leave your personal `ZOTERO_API_KEY` out of that machine's `.env`, or the lab's likes land in
  your library; `config.kcvl.yaml` turns Zotero and Discord off (`zotero.enabled`, `delivery.discord`).
- Use a separate clone on a separate machine (or user account): the database, `state/seen.json`
  and logs live in the clone, and the scheduled jobs have fixed names, so installing a second
  digest on the same account replaces the first one's jobs.

### Ratings and bookmarks
Rate papers 1–5 stars (press `1`–`5` on the selected row) and bookmark them with 🔖 (`b`).
Like with ♥ (`l`). Liked, bookmarked and rated papers are protected from FIFO deletion. Search with `is:saved`,
`is:liked`, `is:rated`, `rating>=4`, or the 🔖 saved checkbox. Likes, bookmarks and ratings ≥ `feedback.min_rating` become positive examples for scoring
(a bookmark you rated lower doesn't).

- **Only the local site can change them**; they're saved straight into the database.
- **GitHub Pages is read-only.** Each publish includes `data/prefs.json` (your ratings and
  bookmarks) so the public site shows the same picks, and rated/bookmarked papers are always
  included even beyond `site_max_papers`. To push new picks right away instead of waiting for the
  next run: `.venv/bin/python run.py --site-only`.

Liked, bookmarked and highly rated papers become positive examples in later scoring runs.

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
Settings: `zotero:` in `config.yaml`; `enabled: false` turns it off even when `.env` has a key.

### BibTeX
**Copy BibTeX** in an expanded row (both sites) gives a Google Scholar-style entry
(`@article`, `journal={arXiv preprint arXiv:<id>}`), which every bibliography style prints in full.
It's built locally by plain code (no network, no LLM) from arXiv's own title, author list and id,
with acronyms such as `{NeRF}` and `{4D}` braced so styles don't lowercase them. When a paper is
later published at a venue, replace the entry with the venue's. `.venv/bin/python run.py --export-bib refs.bib` writes
all liked/bookmarked papers. For a `.bib` that stays in sync with your library, use Zotero's
Better BibTeX plugin on the `arxiv-digest` collection.

## Tuning (`config.yaml`)
- **`interest_profile`** matters most. Be specific about what's core, adjacent and unwanted.
- **`seed_papers`** are titles that are "exactly my taste".
- **`broad_examples`** are papers you value for field-wide importance rather than topic (Adam,
  the Transformer, …). They only teach the impact score, so they don't pull relevance away from
  your subfield. Add the off-topic papers you put on your reading list.
- **`ranking.quality_weight`** (0–1): how much estimated quality matters. Final relevance is
  scaled by `1 − w + w·quality/10`, so with the default 0.3 a zero-quality paper keeps 70% of
  its relevance and a 10-quality paper keeps all of it. Quality never rescues an irrelevant
  paper. `0` ranks by relevance only. The quality score is judged from the abstract (and the
  introduction for the top 15) by an LLM, so treat it as a noisy signal: it rewards specific,
  evidenced claims and penalizes vague or hype-heavy ones, not author or lab prestige.
- **`ranking.impact_min` / `impact_weight`**: big or foundational papers are kept *regardless
  of your interests*. The scorer rates each paper's field-wide impact: would a broad CV /
  graphics / DL researcher read it outside their subfield? General building blocks (optimizers,
  normalization, attention), new generative frameworks, theory behind common practice, and
  widely usable models or datasets count, from any field; prestige alone doesn't. Because Haiku
  judging this from an abstract is the weak step, the summarizer re-judges the top
  `impact_review_n` (20) papers by impact in one call (≈ $0.03/day; 0 turns it off). At
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
