#!/usr/bin/env bash
# Install a cron job: Mon–Fri 14:03 and 16:03 Korea time (16:03 is the catch-up run).
# Requires the machine clock to be KST (cron has no portable timezone support).
#
#   scripts/install_cron.sh            install / update
#   scripts/install_cron.sh --remove   uninstall
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="# arxiv-digest"

current="$(crontab -l 2>/dev/null | grep -vF "$TAG" || true)"

if [ "${1:-}" = "--remove" ]; then
  printf '%s\n' "$current" | sed '/./,$!d' | crontab -
  echo "removed arxiv-digest cron entry"
  exit 0
fi

if [ "$(date +%z)" != "+0900" ]; then
  echo "ERROR: machine timezone is $(date +%Z) ($(date +%z)), not KST." >&2
  echo "       Either set it (sudo timedatectl set-timezone Asia/Seoul) or edit the hours" >&2
  echo "       in this script to your local equivalent of 14:03 / 16:03 KST." >&2
  exit 1
fi

chmod +x "$REPO/scripts/run_daily.sh"
{
  [ -n "$current" ] && printf '%s\n' "$current"
  echo "3 14,16 * * 1-5 $REPO/scripts/run_daily.sh $TAG"
} | crontab -

echo "installed:"
crontab -l | grep -F "$TAG"
echo
echo "Run once now:   $REPO/scripts/run_daily.sh"
echo "Watch the log:  tail -f $REPO/logs/\$(date +%Y-%m).log"
