#!/usr/bin/env bash
# Install the daily schedule with whatever this machine has:
#   macOS → launchd · Linux with systemd → a user timer · otherwise → cron.
# Takes the same flags as the installers: --serve (also keep the local site running), --remove,
# --config FILE (another config, e.g. config.lab.yaml), --host ADDR / --port N (with --serve).
#   scripts/install.sh --serve --config config.lab.yaml --host 0.0.0.0   # a shared lab server
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(uname -s)" = "Darwin" ]; then
  exec /bin/bash "$DIR/install_launchd.sh" "$@"
elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  exec /bin/bash "$DIR/install_systemd.sh" "$@"
else
  # cron has no --serve: pass on the rest and say how to start the site by hand
  args=(); serve=(--serve)
  while [ $# -gt 0 ]; do
    case "$1" in
      --serve) has_serve=1 ;;
      --host|--port) serve+=("$1" "${2:-}"); shift ;;
      --host=*|--port=*) serve+=("$1") ;;
      --config) args+=("$1" "${2:-}"); serve+=("$1" "${2:-}"); shift ;;
      --config=*) args+=("$1"); serve+=("$1") ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  [ -n "${has_serve:-}" ] && echo "NOTE: no systemd here; run the local site yourself: tmux + .venv/bin/python run.py ${serve[*]}" >&2
  exec /bin/bash "$DIR/install_cron.sh" ${args[@]+"${args[@]}"}
fi
