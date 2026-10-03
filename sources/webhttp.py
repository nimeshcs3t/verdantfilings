"""Polite HTTP for website-based sources: a pause between requests, and if a site refuses (403/429), the
source pauses itself for some hours instead of retrying."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from .base import SourceBusy

log = logging.getLogger(__name__)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")


class PoliteClient:
    def __init__(self, name: str, pause: float = 0.5, block_hours: int = 6, headers: dict | None = None):
        self.name, self.pause, self.block_hours = name, pause, block_hours
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", **(headers or {})})
        self._last = 0.0

    def _state_key(self) -> str:
        return f"{self.name}_blocked_until"

    def paused_until(self) -> datetime | None:
        try:
            from core.usage import get_state
            value = get_state(self._state_key())
            until = datetime.fromisoformat(value) if value else None
        except Exception:
            return None
        return until if until and until > datetime.now(timezone.utc) else None

    def request(self, method: str, url: str, **kw) -> requests.Response:
        if self.paused_until():
            raise SourceBusy(f"{self.name} paused")
        wait = self.pause - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        r = self.session.request(method, url, timeout=kw.pop("timeout", 60), **kw)
        if r.status_code in (403, 429):
            try:
                from core.usage import set_state
                until = datetime.now(timezone.utc) + timedelta(hours=self.block_hours)
                set_state(self._state_key(), until.isoformat())
            except Exception:
                pass
            log.warning("%s refused a request (status %s); paused for %d hours", self.name, r.status_code, self.block_hours)
            raise SourceBusy(f"{self.name} refused the request")
        r.raise_for_status()
        return r

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
