#!/usr/bin/env bash
# Install a systemd *user* timer: Mon–Fri 14:03 and 16:03 Korea time.
# Times are written in Asia/Seoul directly, whatever the machine's own timezone.
# Persistent=true: if the machine was off at run time, it runs at next boot.
#
#   scripts/install_timer.sh            install / update
#   scripts/install_timer.sh --remove   uninstall
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
NAME=arxiv-digest

if [ "${1:-}" = "--remove" ]; then
  systemctl --user disable --now "$NAME.timer" 2>/dev/null || true
  rm -f "$UNIT_DIR/$NAME.service" "$UNIT_DIR/$NAME.timer"
  systemctl --user daemon-reload
  echo "removed $NAME timer"
  exit 0
fi

command -v systemctl >/dev/null || { echo "systemd not found — use the cron line in README instead"; exit 1; }
chmod +x "$REPO/scripts/run_daily.sh"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/$NAME.service" <<EOF
[Unit]
Description=arXiv daily digest
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO
ExecStart=$REPO/scripts/run_daily.sh
TimeoutStartSec=45min
EOF

cat > "$UNIT_DIR/$NAME.timer" <<EOF
[Unit]
Description=arXiv daily digest — 14:03 and 16:03 KST, Mon–Fri

[Timer]
# main run, shortly after arXiv's feed refresh
OnCalendar=Mon..Fri *-*-* 14:03:00 Asia/Seoul
# catch-up in case the feed was late (does nothing if the first run already got today's papers)
OnCalendar=Mon..Fri *-*-* 16:03:00 Asia/Seoul
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$NAME.timer"

# keep user timers running when you're logged out (needs permission on shared servers)
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  if loginctl enable-linger "$USER" 2>/dev/null; then
    echo "enabled lingering: the timer keeps running after you log out"
  else
    echo
    echo "NOTE: could not enable lingering. Without it, the timer only runs while you're logged in."
    echo "      Ask an admin to run:  sudo loginctl enable-linger $USER"
    echo "      (or use the cron line in README instead)"
  fi
fi

echo
systemctl --user list-timers "$NAME.timer" --no-pager
echo
echo "Run once now:   systemctl --user start $NAME.service"
echo "Watch the log:  tail -f $REPO/logs/\$(date +%Y-%m).log"
