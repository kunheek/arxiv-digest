"""Load settings from a local .env file (for running on your own machine).

Real environment variables always win over .env.

Supported keys: ANTHROPIC_API_KEY, DISCORD_WEBHOOK, AUTO_PULL.
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
