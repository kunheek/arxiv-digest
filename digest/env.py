"""Load settings from a local .env file (for running on your own machine).

Real environment variables always win over .env, so GitHub Actions or a
shell `export` still work unchanged.

Supported keys: ANTHROPIC_API_KEY, DISCORD_WEBHOOK, SHEET_ID, and either
GOOGLE_CREDS (the JSON itself) or GOOGLE_CREDS_FILE (path to the key file).
"""
from __future__ import annotations

import os


def load_env(path: str = ".env") -> None:
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip().removeprefix("export ").strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                if key and not os.environ.get(key):
                    os.environ[key] = val

    # GOOGLE_CREDS_FILE=/path/key.json → GOOGLE_CREDS=<file contents>
    creds_file = os.environ.get("GOOGLE_CREDS_FILE", "").strip()
    if creds_file and not os.environ.get("GOOGLE_CREDS"):
        creds_file = os.path.expanduser(creds_file)
        if not os.path.exists(creds_file):
            raise FileNotFoundError(f"GOOGLE_CREDS_FILE points to a missing file: {creds_file}")
        with open(creds_file) as f:
            os.environ["GOOGLE_CREDS"] = f.read()
