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

# bootout returns before the job is gone, and bootstrapping it again too soon fails with
# "Bootstrap failed: 5: Input/output error", so wait (up to 10 s) until launchd has let go
stop() {
  launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
  for _ in $(seq 50); do launchctl print "$DOMAIN/$1" >/dev/null 2>&1 || return 0; sleep 0.2; done
}
unload() { stop "$1"; rm -f "$AGENTS/$1.plist"; }
load() { stop "$1"; launchctl bootstrap "$DOMAIN" "$AGENTS/$1.plist"; }
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

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

# launchd uses the Mac's own clock, so convert 14:03/16:03 KST to it. Where the Mac's timezone
# has summer time, schedule the hours for both offsets (launchd can't follow another timezone);
# run_daily.sh drops the firing that lands at 13:03 KST, and the extra late one finds nothing new.
hours=""; offsets=""; n_off=0
for mmdd in 0115 0715; do
  sys_off="$(env -u TZ date -j -f %m%d "$mmdd" +%z)"
  case " $offsets " in *" $sys_off "*) continue ;; esac
  offsets+=" $sys_off"; n_off=$(( n_off + 1 ))
  sign=1; [ "${sys_off:0:1}" = "-" ] && sign=-1
  off_h=$(( sign * 10#${sys_off:1:2} )); off_m="${sys_off:3:2}"
  h1=$(( 14 - 9 + off_h )); h2=$(( 16 - 9 + off_h ))
  if [ "$off_m" != "00" ] || [ $h1 -lt 0 ] || [ $h2 -gt 23 ]; then
    echo "ERROR: timezone offset $sys_off needs a day shift or half-hour offset; edit this script by hand." >&2
    exit 1
  fi
  for h in $h1 $h2; do case " $hours " in *" $h "*) ;; *) hours+=" $h" ;; esac; done
done
env_xml=""
[ $n_off -gt 1 ] && env_xml="<key>EnvironmentVariables</key><dict><key>SKIP_KST_HOUR</key><string>13</string></dict>"

times=""
for day in 1 2 3 4 5; do
  for h in $hours; do
    times+="    <dict><key>Weekday</key><integer>$day</integer><key>Hour</key><integer>$h</integer><key>Minute</key><integer>3</integer></dict>
"
  done
done

mkdir -p "$AGENTS" "$REPO/logs"
RX="$(xml "$REPO")"; PX="$(xml "$PY")"   # paths as plist text (a folder may contain & or <)
chmod +x "$REPO/scripts/run_daily.sh"
cat > "$AGENTS/$DAILY.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$DAILY</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$RX/scripts/run_daily.sh</string></array>
  <key>WorkingDirectory</key><string>$RX</string>
  $env_xml
  <key>StartCalendarInterval</key>
  <array>
$times  </array>
  <key>StandardOutPath</key><string>$RX/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$RX/logs/launchd.log</string>
</dict>
</plist>
EOF
load "$DAILY"
echo "timezone offset${offsets} → daily run Mon–Fri at:$(printf ' %s:03' $(printf '%s\n' $hours | sort -n)) (= 14:03 and 16:03 KST)"

if [ "${1:-}" = "--serve" ]; then
  cat > "$AGENTS/$SERVE.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$SERVE</string>
  <key>ProgramArguments</key>
  <array><string>$PX</string><string>run.py</string><string>--serve</string></array>
  <key>WorkingDirectory</key><string>$RX</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$RX/logs/serve.log</string>
  <key>StandardErrorPath</key><string>$RX/logs/serve.log</string>
</dict>
</plist>
EOF
  stop "$SERVE"   # free the port if an older copy of this job holds it
  if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "WARNING: something else is listening on port 8000 (often a run.py --serve in a terminal):" >&2
    lsof -nP -iTCP:8000 -sTCP:LISTEN >&2 || true
    echo "         stop it; until then the site job retries once a minute (see logs/serve.log)." >&2
  fi
  load "$SERVE"
  echo "local site: http://127.0.0.1:8000 (starts at login, restarts if it crashes)"
fi

echo
echo "Run once now:   launchctl kickstart $DOMAIN/$DAILY"
echo "Watch the log:  tail -f $REPO/logs/\$(date +%Y-%m).log"
