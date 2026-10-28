# Option parsing shared by the installers (sourced, not run). Needs $REPO. Sets:
#   REMOVE, SERVE_SITE   0/1
#   CONFIG               absolute path of --config FILE, or "" for the default config.yaml
#   HOST, PORT           where --serve listens (127.0.0.1:8000 unless --host/--port)
#   DAILY_ARGS           extra arguments for run_daily.sh (empty unless --config)
#   SERVE_ARGS           arguments for run.py that start the site
REMOVE=0; SERVE_SITE=0; CONFIG=""; HOST=127.0.0.1; PORT=8000; host_set=0
while [ $# -gt 0 ]; do
  case "$1" in
    --remove) REMOVE=1 ;;
    --serve) SERVE_SITE=1 ;;
    --config) CONFIG="${2:?--config needs a file}"; shift ;;
    --config=*) CONFIG="${1#*=}" ;;
    --host) HOST="${2:?--host needs an address}"; host_set=1; shift ;;
    --host=*) HOST="${1#*=}"; host_set=1 ;;
    --port) PORT="${2:?--port needs a number}"; host_set=1; shift ;;
    --port=*) PORT="${1#*=}"; host_set=1 ;;
    *) echo "ERROR: unknown option $1 (use --serve, --config FILE, --host ADDR, --port N, --remove)" >&2; exit 2 ;;
  esac
  shift
done

case "$PORT" in ''|*[!0-9]*) echo "ERROR: --port must be a number, not '$PORT'" >&2; exit 2 ;; esac
if [ $host_set = 1 ] && [ $SERVE_SITE = 0 ] && [ $REMOVE = 0 ]; then
  echo "ERROR: --host/--port only apply with --serve" >&2; exit 2
fi

# a relative --config is looked up from the current directory, then from the repo
if [ -n "$CONFIG" ]; then
  if [ -f "$CONFIG" ]; then
    CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"
  elif [ "${CONFIG#/}" = "$CONFIG" ] && [ -f "$REPO/$CONFIG" ]; then
    CONFIG="$REPO/$CONFIG"
  elif [ $REMOVE = 0 ]; then
    echo "ERROR: config file $CONFIG not found" >&2; exit 2
  fi
fi

DAILY_ARGS=(); SERVE_ARGS=(run.py --serve)
[ -n "$CONFIG" ] && DAILY_ARGS=(--config "$CONFIG") && SERVE_ARGS+=(--config "$CONFIG")
SERVE_ARGS+=(--host "$HOST" --port "$PORT")

# the site's address, for the installers' closing message
site_url() {
  case "$HOST" in
    0.0.0.0|::) echo "http://$(hostname):$PORT" ;;
    *:*) echo "http://[$HOST]:$PORT" ;;
    *) echo "http://$HOST:$PORT" ;;
  esac
}
# the site has no login, so say so when it listens beyond this machine
open_warning() {
  case "$HOST" in
    127.0.0.1|localhost|::1) ;;
    *) echo "NOTE: the site listens on $HOST and has no login; anyone who can reach port $PORT can read it and change ratings." >&2 ;;
  esac
}
