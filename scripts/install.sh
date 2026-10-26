#!/usr/bin/env bash
# Install the daily schedule with whatever this machine has:
#   macOS → launchd · Linux with systemd → a user timer · otherwise → cron.
# Takes the same flags as the installers: --serve (also keep the local site running), --remove.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(uname -s)" = "Darwin" ]; then
  exec /bin/bash "$DIR/install_launchd.sh" "$@"
elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  exec /bin/bash "$DIR/install_systemd.sh" "$@"
else
  [ "${1:-}" = "--serve" ] && echo "NOTE: no systemd here; run the local site yourself (tmux + run.py --serve)." >&2
  [ "${1:-}" = "--serve" ] && shift
  exec /bin/bash "$DIR/install_cron.sh" "$@"
fi
