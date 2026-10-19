#!/usr/bin/env bash
# Install a cron job: Mon–Fri 14:03 and 16:03 Korea time (16:03 is the catch-up run).
# The hours are converted to the machine's system timezone (cron ignores your shell's TZ).
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

# cron runs in the SYSTEM timezone, not your shell's $TZ, so convert 14:03/16:03 KST to it
sys_off="$(env -u TZ date +%z)"                      # e.g. +0000 or +0900
sign=1; [ "${sys_off:0:1}" = "-" ] && sign=-1
off_h=$(( sign * 10#${sys_off:1:2} )); off_m="${sys_off:3:2}"
h1=$(( 14 - 9 + off_h )); h2=$(( 16 - 9 + off_h ))
if [ "$off_m" != "00" ] || [ $h1 -lt 0 ] || [ $h2 -gt 23 ]; then
  echo "ERROR: system timezone offset $sys_off needs a day shift or half-hour offset;" >&2
  echo "       edit the cron line in this script by hand." >&2
  exit 1
fi
HOURS="$h1,$h2"

chmod +x "$REPO/scripts/run_daily.sh"
{
  [ -n "$current" ] && printf '%s\n' "$current"
  echo "3 $HOURS * * 1-5 $REPO/scripts/run_daily.sh $TAG"
} | crontab -

echo "system timezone offset $sys_off → cron hours $HOURS (= 14:03 and 16:03 KST)"
echo "installed:"
crontab -l | grep -F "$TAG"
echo
echo "Run once now:   $REPO/scripts/run_daily.sh"
echo "Watch the log:  tail -f $REPO/logs/\$(date +%Y-%m).log"
