#!/usr/bin/env bash
# Daily entry point on your own machine (called by a systemd timer or cron on Linux, launchd on macOS).
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

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# launchd fires at both the standard- and summer-time hours for 14:03/16:03 KST (it can't follow
# another timezone), so the one that lands an hour early is dropped here
[ -n "${SKIP_KST_HOUR:-}" ] && [ "$(TZ=Asia/Seoul date +%H)" = "$SKIP_KST_HOUR" ] && exit 0

# only one run at a time (main + catch-up never overlap). flock(2) on fd 9 via Python, because
# macOS has no flock(1): the lock belongs to the open file, so it stays held by this shell (and
# run.py) after Python exits, and the kernel drops it when the run ends, even if it was killed.
exec 9>"logs/.lock"
"$PY" -c '
import fcntl, sys
try: fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: sys.exit(75)'
if [ $? -eq 75 ]; then
  echo "$(date '+%F %T') another run is in progress, skipping" >> "$LOG"
  exit 0
fi

{
  echo
  echo "===== $(TZ=Asia/Seoul date '+%F %T KST')  run_daily.sh $* ====="
  if [ "${AUTO_PULL:-1}" = "1" ] && [ -d .git ]; then
    git pull --ff-only --quiet 2>&1 || echo "WARNING: git pull failed; running with local code"
  fi
} >> "$LOG" 2>&1

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
