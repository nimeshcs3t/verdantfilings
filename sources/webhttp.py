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
    def __init__(self, name: str, pause: float = 0.5, block_hours: int = 6, headers: dict | None = None,
                 timeout: int = 60, network_strikes: int = 0):
        """network_strikes: after this many timeouts / dropped connections in a row, pause the source like a refusal
        (0 = never). For sites that throttle by going silent instead of answering 429."""
        self.name, self.pause, self.block_hours = name, pause, block_hours
        self.timeout, self.network_strikes, self._strikes = timeout, network_strikes, 0
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
        try:
            r = self.session.request(method, url, timeout=kw.pop("timeout", self.timeout), **kw)
        except (requests.Timeout, requests.ConnectionError) as exc:
            self._strikes += 1
            if self.network_strikes and self._strikes >= self.network_strikes:
                self._strikes = 0
                self._block(f"stopped answering ({exc.__class__.__name__})")
                raise SourceBusy(f"{self.name} isn't answering") from exc
            raise
        self._strikes = 0
        if r.status_code in (403, 429):
            self._block(f"refused a request (status {r.status_code})")
            raise SourceBusy(f"{self.name} refused the request")
        r.raise_for_status()
        return r

    def _block(self, why: str) -> None:
        try:
            from core.usage import set_state
            until = datetime.now(timezone.utc) + timedelta(hours=self.block_hours)
            set_state(self._state_key(), until.isoformat())
        except Exception:
            pass
        log.warning("%s %s; paused for %d hours", self.name, why, self.block_hours)

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
