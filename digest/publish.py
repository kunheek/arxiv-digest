"""Publish the built site to the gh-pages branch (single force-pushed commit,
so the repo never accumulates history)."""
from __future__ import annotations

import os
import subprocess
import tempfile


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def publish(site_dir: str = "site", branch: str = "gh-pages", repo_dir: str = ".") -> None:
    remote = _git(["remote", "get-url", "origin"], repo_dir).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "w")
        subprocess.run(["cp", "-a", site_dir, work], check=True)
        _git(["init", "-q", "-b", branch], work)
        _git(["add", "-A"], work)
        _git(["-c", "user.name=digest-bot", "-c", "user.email=digest-bot@users.noreply.github.com",
              "commit", "-q", "-m", "publish site"], work)
        _git(["push", "-q", "-f", remote, f"HEAD:{branch}"], work)


def site_url(cfg: dict, repo_dir: str = ".") -> str:
    """delivery.site_url, or the GitHub Pages address derived from the origin remote."""
    url = cfg.get("delivery", {}).get("site_url", "")
    if url:
        return url
    try:
        remote = _git(["remote", "get-url", "origin"], repo_dir).stdout.strip()
    except Exception:
        return ""
    import re
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote)
    return f"https://{m[1].lower()}.github.io/{m[2]}/" if m else ""
