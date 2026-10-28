#!/usr/bin/env bash
# Linux counterpart of install_launchd.sh: run the digest Mon–Fri 14:03 and 16:03 Korea time with
# a systemd user timer. Unlike cron, the timer is Persistent: a run that was due while the machine
# was off or asleep starts as soon as it is back. Linger is enabled so it runs without a login.
#
#   scripts/install_systemd.sh            install / update the daily run
#   scripts/install_systemd.sh --serve    also keep the local site running on http://127.0.0.1:8000
#   scripts/install_systemd.sh --remove   uninstall both
# Options: --config FILE (use another config, e.g. config.lab.yaml), and with --serve
# --host ADDR / --port N (--host 0.0.0.0 makes the site reachable from other machines).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DAILY=arxiv-digest-daily
SERVE=arxiv-digest-serve
source "$REPO/scripts/installer_args.sh"
# one ExecStart argument: quoted, with systemd's % and $ specifiers escaped
sdq() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//%/%%}"; s="${s//\$/\$\$}"; printf ' "%s"' "$s"; }
sdargs() { local a; for a in "$@"; do sdq "$a"; done; }

command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1 || {
  echo "ERROR: no systemd user session here; use scripts/install_cron.sh instead." >&2
  exit 1
}

if [ $REMOVE = 1 ]; then
  systemctl --user disable --now "$DAILY.timer" "$SERVE.service" 2>/dev/null || true
  rm -f "$UNITS/$DAILY.timer" "$UNITS/$DAILY.service" "$UNITS/$SERVE.service"
  systemctl --user daemon-reload
  echo "removed the arxiv-digest systemd units"
  exit 0
fi

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "ERROR: $PY not found; create the venv first (see README)." >&2; exit 1; }

mkdir -p "$UNITS" "$REPO/logs"
chmod +x "$REPO/scripts/run_daily.sh"

cat > "$UNITS/$DAILY.service" <<EOF
[Unit]
Description=arXiv daily digest

[Service]
Type=oneshot
WorkingDirectory=$REPO
ExecStart=/bin/bash $REPO/scripts/run_daily.sh$(sdargs ${DAILY_ARGS[@]+"${DAILY_ARGS[@]}"})
EOF

# OnCalendar takes the timezone itself, so no conversion to the system timezone is needed
cat > "$UNITS/$DAILY.timer" <<EOF
[Unit]
Description=arXiv daily digest, Mon–Fri 14:03 and 16:03 KST

[Timer]
OnCalendar=Mon..Fri *-*-* 14,16:03:00 Asia/Seoul
Persistent=true

[Install]
WantedBy=timers.target
EOF

if [ $SERVE_SITE = 1 ]; then
  cat > "$UNITS/$SERVE.service" <<EOF
[Unit]
Description=arXiv digest local site

[Service]
WorkingDirectory=$REPO
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY$(sdargs "${SERVE_ARGS[@]}")
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
fi

systemctl --user daemon-reload
if ! systemd-analyze calendar "Mon..Fri *-*-* 14,16:03:00 Asia/Seoul" >/dev/null 2>&1; then
  # systemd < 239 has no timezone in OnCalendar: fall back to the system timezone
  sys_off="$(env -u TZ date +%z)"
  sign=1; [ "${sys_off:0:1}" = "-" ] && sign=-1
  off_h=$(( sign * 10#${sys_off:1:2} )); off_m="${sys_off:3:2}"
  h1=$(( 14 - 9 + off_h )); h2=$(( 16 - 9 + off_h ))
  if [ "$off_m" != "00" ] || [ $h1 -lt 0 ] || [ $h2 -gt 23 ]; then
    echo "ERROR: timezone offset $sys_off needs a day shift or half-hour offset; edit $UNITS/$DAILY.timer by hand." >&2
    exit 1
  fi
  sed -i "s|^OnCalendar=.*|OnCalendar=Mon..Fri *-*-* $h1,$h2:03:00|" "$UNITS/$DAILY.timer"
  systemctl --user daemon-reload
fi
systemctl --user enable --now "$DAILY.timer"
echo "installed timer: $(grep ^OnCalendar "$UNITS/$DAILY.timer" | cut -d= -f2-)"

if [ $SERVE_SITE = 1 ]; then
  systemctl --user enable "$SERVE.service"
  systemctl --user restart "$SERVE.service"
  echo "local site: $(site_url) (starts at boot, restarts if it crashes)"
  open_warning
  echo "site log:       journalctl --user -u $SERVE -f"
fi

# without linger, user units stop at logout and don't start until you log in
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  loginctl enable-linger "$USER" 2>/dev/null \
    || echo "NOTE: run 'sudo loginctl enable-linger $USER' so the jobs run while you're logged out." >&2
fi

echo
echo "Next runs:      systemctl --user list-timers $DAILY.timer"
echo "Run once now:   systemctl --user start $DAILY.service"
echo "Watch the log:  tail -f $REPO/logs/\$(date +%Y-%m).log"
