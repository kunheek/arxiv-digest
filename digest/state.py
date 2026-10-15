"""Remember which papers were already processed (GitHub runners have no memory)."""
from __future__ import annotations

import datetime as dt
import json
import os

KEEP_DAYS = 30


def load(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save(path: str, seen: dict[str, str], today: str) -> None:
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(seen, f, indent=0, sort_keys=True)
