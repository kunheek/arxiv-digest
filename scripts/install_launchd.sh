#!/usr/bin/env bash
# macOS counterpart of install_cron.sh: run the digest Mon–Fri 14:03 and 16:03 Korea time with
# launchd. Unlike cron, launchd starts a run that was due while the Mac slept as soon as it
# wakes (a run due while it was shut down is still missed).
#
#   scripts/install_launchd.sh            install / update the daily run
#   scripts/install_launchd.sh --serve    also keep the local site running on http://127.0.0.1:8000
#   scripts/install_launchd.sh --remove   uninstall both
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DAILY=com.arxiv-digest.daily
SERVE=com.arxiv-digest.serve
DOMAIN="gui/$(id -u)"

unload() { launchctl bootout "$DOMAIN/$1" 2>/dev/null || true; rm -f "$AGENTS/$1.plist"; }
load() { launchctl bootout "$DOMAIN/$1" 2>/dev/null || true; launchctl bootstrap "$DOMAIN" "$AGENTS/$1.plist"; }

if [ "${1:-}" = "--remove" ]; then
  unload "$DAILY"; unload "$SERVE"
  echo "removed the arxiv-digest launchd jobs"
  exit 0
fi

case "$REPO/" in
  "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
    echo "WARNING: macOS stops background jobs from reading $REPO (privacy protection)." >&2
    echo "         Move the repo to e.g. ~/arxiv-digest, or the scheduled runs will fail." >&2 ;;
esac
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "ERROR: $PY not found; create the venv first (see README)." >&2; exit 1; }

# launchd uses the Mac's own timezone, so convert 14:03/16:03 KST to it
sys_off="$(env -u TZ date +%z)"
sign=1; [ "${sys_off:0:1}" = "-" ] && sign=-1
off_h=$(( sign * 10#${sys_off:1:2} )); off_m="${sys_off:3:2}"
h1=$(( 14 - 9 + off_h )); h2=$(( 16 - 9 + off_h ))
if [ "$off_m" != "00" ] || [ $h1 -lt 0 ] || [ $h2 -gt 23 ]; then
  echo "ERROR: timezone offset $sys_off needs a day shift or half-hour offset; edit this script by hand." >&2
  exit 1
fi

times=""
for day in 1 2 3 4 5; do
  for h in $h1 $h2; do
    times+="    <dict><key>Weekday</key><integer>$day</integer><key>Hour</key><integer>$h</integer><key>Minute</key><integer>3</integer></dict>
"
  done
done

mkdir -p "$AGENTS" "$REPO/logs"
chmod +x "$REPO/scripts/run_daily.sh"
cat > "$AGENTS/$DAILY.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$DAILY</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$REPO/scripts/run_daily.sh</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key>
  <array>
$times  </array>
  <key>StandardOutPath</key><string>$REPO/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd.log</string>
</dict>
</plist>
EOF
load "$DAILY"
echo "timezone offset $sys_off → daily run Mon–Fri at $h1:03 and $h2:03 (= 14:03 and 16:03 KST)"

if [ "${1:-}" = "--serve" ]; then
  cat > "$AGENTS/$SERVE.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$SERVE</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>run.py</string><string>--serve</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO/logs/serve.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/serve.log</string>
</dict>
</plist>
EOF
  load "$SERVE"
  echo "local site: http://127.0.0.1:8000 (starts at login, restarts if it crashes)"
fi

echo
echo "Run once now:   launchctl kickstart $DOMAIN/$DAILY"
echo "Watch the log:  tail -f $REPO/logs/\$(date +%Y-%m).log"
