# arXiv daily digest

Every weekday this scans new **cs.CV, cs.CG, cs.LG and stat.ML** papers, scores each one
against your interests with Claude, boosts papers from researchers and labs you watch,
summarizes the best ones, and posts them to **Discord** and **Google Sheets**.

```
arXiv RSS (≈ several hundred new papers/day)
   │
   ├─ 1. Haiku scores every abstract against your profile (0–10)
   ├─ 2. Watchlist: author names + affiliations (from arXiv HTML, top ~40 only)
   ├─ 3. Rank = interest + author bonus + institution bonus
   ├─ 4. Sonnet writes TL;DR + "why you should care" for the top 15
   └─ 5. Discord (top 10) · Google Sheet (everything ≥ 3) · digest.md
```

Expected cost: roughly **$0.20–0.60 per day**, so about $5–15 a month.
Every run prints its actual cost.

---

## Setup (≈ 30 minutes, one time)

### 1. Get a Claude API key
1. Go to <https://platform.claude.com> → **API keys** → **Create key**.
2. Check that your account has credit. The digest uses the **API**, which is billed
   separately from chat usage. If your monthly credit only covers the Claude app or
   Claude Code, add a small API balance instead.

### 2. Try it on your Mac (optional, recommended)
```bash
cd arxiv-digest
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python run.py --dry-run --max-papers 40   # costs about a cent; writes digest.md
open digest.md
```
Run this on a weekday afternoon (Korea time). arXiv is empty on weekends.

### 3. Create a Discord webhook
In your Discord server, go to **Server Settings → Integrations → Webhooks → New Webhook**.
Pick a channel, then **Copy Webhook URL**.

### 4. Create the Google Sheet and a service account
1. Create an empty Google Sheet. Its ID is the long string in the URL:
   `docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`
2. Go to <https://console.cloud.google.com>, create a project (any name), then go to
   **APIs & Services → Enable APIs** and enable **Google Sheets API**.
3. Go to **IAM & Admin → Service Accounts → Create service account** (no roles needed).
   Open it, then **Keys → Add key → JSON**. A `.json` file downloads.
4. Open that JSON file and copy the `client_email`. **Share your Sheet** with that address
   as **Editor**.

### 5. Put the project on GitHub
```bash
cd arxiv-digest
git init && git add . && git commit -m "arXiv digest"
gh repo create arxiv-digest --private --source=. --push   # or create it on github.com and push
```

### 6. Add secrets
In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your `sk-ant-...` key |
| `DISCORD_WEBHOOK` | the webhook URL |
| `GOOGLE_CREDS` | the **entire contents** of the service-account JSON file |
| `SHEET_ID` | the Sheet ID from step 4.1 |

Then delete the JSON file from your Downloads folder.

### 7. Test it
Go to the **Actions** tab → **daily-arxiv-digest** → **Run workflow**.
Tick *dry run* the first time; the digest appears on the run's summary page.
Then run it once without dry run and check Discord and the Sheet.

After that it runs on its own **Mon–Fri at about 15:07 KST**, shortly after arXiv's daily
feed refresh. GitHub sometimes starts scheduled runs a few minutes late.

---

## Tuning
Everything lives in `config.yaml`. Edit it on github.com and commit; the next run uses it.

- **`interest_profile`** matters most. Be specific about what's core, adjacent, and unwanted.
- **`seed_papers`** are titles that are "exactly my taste".
- **`watch_authors` / `watch_institutions`** add bonuses. Aliases use `|`, e.g. `"ETH Zurich|ETH Zürich"`.
  Keep the bonuses modest so good papers from smaller labs still surface.
- **Feedback loop:** fill in the **my rating (1-5)** column in the Sheet. Papers you rate
  4–5 become examples the scorer learns your taste from on later runs.
- **Cost knobs:** `summarize_top_k`, `enrich_top_n`. Switching the summarizer to
  `claude-opus-5-5` roughly doubles summary cost.

## Notes
- `state/seen.json` remembers processed papers so nothing is posted twice. The workflow
  commits it after each run, which also keeps the scheduled workflow from being paused for
  inactivity.
- Affiliations come from the paper's arXiv HTML page. Papers without an HTML version
  still get scored; they just can't earn the institution bonus.
- Run the offline tests with `pip install pytest && python -m pytest -q`.
