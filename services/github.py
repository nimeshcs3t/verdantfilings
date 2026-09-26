"""Start the GitHub Actions sync job from the website, so new companies and overviews arrive in
a minute or two instead of waiting for the next scheduled run. Optional: needs GH_TOKEN and GH_REPO."""
from __future__ import annotations

import threading
import time

import requests

from core.config import get_secret

_last_trigger = 0.0
_lock = threading.Lock()
MIN_GAP_SECONDS = 90


def configured() -> bool:
    return bool(get_secret("GH_TOKEN") and get_secret("GH_REPO"))


def trigger_sync() -> bool:
    """Returns True if a run was started (or one was started in the last 90 seconds)."""
    global _last_trigger
    if not configured():
        return False
    with _lock:
        if time.time() - _last_trigger < MIN_GAP_SECONDS:
            return True
        try:
            r = requests.post(
                f"https://api.github.com/repos/{get_secret('GH_REPO')}/actions/workflows/poll.yml/dispatches",
                headers={"Authorization": f"Bearer {get_secret('GH_TOKEN')}",
                         "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                json={"ref": get_secret("GH_BRANCH", "main")}, timeout=15)
        except requests.RequestException:
            return False
        if r.status_code == 204:
            _last_trigger = time.time()
            return True
        return False
