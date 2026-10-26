#!/usr/bin/env bash
# Daily entry point on your own machine (called by cron on Linux, launchd on macOS).
#   - pulls config changes you pushed from elsewhere (AUTO_PULL=1)
#   - runs the digest inside the project's .venv
#   - logs to logs/YYYY-MM.log
#   - posts to Discord if the run fails
# Extra arguments are passed to run.py, e.g.  scripts/run_daily.sh --dry-run
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
mkdir -p logs
LOG="logs/$(TZ=Asia/Seoul date +%Y-%m).log"

# read AUTO_PULL / DISCORD_WEBHOOK from .env without exporting secrets to git etc.
envval() { [ -f .env ] && grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/^["'\'']|["'\'']$//g'; }
AUTO_PULL="${AUTO_PULL:-$(envval AUTO_PULL)}"
WEBHOOK="${DISCORD_WEBHOOK:-$(envval DISCORD_WEBHOOK)}"

# only one run at a time (main + catch-up never overlap)
skip() { echo "$(date '+%F %T') another run is in progress, skipping" >> "$LOG"; exit 0; }
if command -v flock >/dev/null 2>&1; then
  exec 9>"logs/.lock"
  flock -n 9 || skip
else  # macOS has no flock(1): an atomic mkdir, taken over if the run holding it has died
  mkdir logs/.lock.d 2>/dev/null || { kill -0 "$(cat logs/.lock.d/pid 2>/dev/null)" 2>/dev/null && skip; }
  echo $$ > logs/.lock.d/pid
  trap 'rm -rf logs/.lock.d' EXIT
fi

{
  echo
  echo "===== $(TZ=Asia/Seoul date '+%F %T KST')  run_daily.sh $* ====="
  if [ "${AUTO_PULL:-1}" = "1" ] && [ -d .git ]; then
    git pull --ff-only --quiet 2>&1 || echo "WARNING: git pull failed; running with local code"
  fi
} >> "$LOG" 2>&1

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

"$PY" run.py "$@" >> "$LOG" 2>&1
status=$?
echo "exit status: $status" >> "$LOG"

if [ $status -ne 0 ] && [ -n "${WEBHOOK:-}" ]; then
  tail_txt="$(tail -n 15 "$LOG" | cut -c1-180)"
  msg="⚠️ **arXiv digest failed** on $(hostname) (exit $status). Last lines of \`$LOG\`:"
  "$PY" - "$WEBHOOK" "$msg" "$tail_txt" <<'EOF' >> "$LOG" 2>&1 || true
import json, sys, urllib.request
url, msg, tail = sys.argv[1:4]
body = json.dumps({"content": f"{msg}\n```\n{tail[-1700:]}\n```"}).encode()
req = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                         "User-Agent": "arxiv-digest"})
urllib.request.urlopen(req, timeout=20)
EOF
fi
exit $status
