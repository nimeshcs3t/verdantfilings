"""Investor page watch: for companies whose exchange can't be read automatically (Tel Aviv, where MAYA blocks
automated access), the app checks the company's own investor relations page and treats each new report or press
release link as a filing: stored, translated if Hebrew, and alerted like any other.

Settings live in bot_state under "irwatch" as JSON: {"IL:SMSH": {"url", "seen": [link hashes], "checked", "links",
"error"}}. The first check only records what is already there, so old documents don't arrive as new."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from core.db import companies, filings, get_engine, utcnow
from core.usage import get_state, set_state

log = logging.getLogger(__name__)
KEY = "irwatch"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0.0.0 Safari/537.36", "Accept-Language": "en,he;q=0.8"}
# Words that mark a link as a report, results, presentation or release (English and Hebrew).
REPORTY = re.compile(r"report|result|financial|quarter|\bq[1-4]\b|annual|interim|presentation|press|release|announce|"
                     r"news|investor|shareholder|meeting|agm|dividend|prospectus|immediate|filing|webcast|earnings|"
                     r"\.pdf\b|דוח|דו\"ח|דיווח|תוצאות|רבעון|שנתי|הודעה|מצגת|אסיפה|דיבידנד|תשקיף|מיידי", re.I)
SKIP = re.compile(r"^(mailto|tel|javascript):|linkedin|facebook|twitter|x\.com|youtube|instagram|cookie|privacy|"
                  r"terms|accessibility|login|careers|contact", re.I)
MAX_NEW = 15                 # per check: a site redesign shouldn't flood the feed
MAX_SEEN = 800


def _all() -> dict:
    try:
        return json.loads(get_state(KEY) or "{}")
    except ValueError:
        return {}


def _save(data: dict) -> None:
    set_state(KEY, json.dumps(data))


def watched() -> dict:
    return _all()


def get(market: str, ticker: str) -> dict | None:
    return _all().get(f"{market}:{ticker}")


def _h(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def links(url: str) -> list[tuple[str, str]]:
    """(absolute url, link text) for report-like links on the page, in page order."""
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    html = r.text[:1_500_000]
    host = urlparse(r.url).netloc.split(":")[0].removeprefix("www.")
    out, seen = [], set()
    for m in re.finditer(r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#][^\"']*)[\"'][^>]*>(.*?)</a>", html, re.S | re.I):
        href, inner = m.group(1).strip(), m.group(2)
        if SKIP.search(href):
            continue
        absu = urldefrag(urljoin(r.url, href))[0]
        text = " ".join(re.sub(r"<[^>]+>", " ", inner).split())
        text = re.sub(r"&nbsp;|&#160;", " ", text).replace("&amp;", "&").strip()
        title_attr = re.search(r"title\s*=\s*[\"']([^\"']+)", m.group(0))
        if len(text) < 6 and title_attr:
            text = title_attr.group(1).strip()
        other = urlparse(absu).netloc.split(":")[0].removeprefix("www.")
        is_doc = bool(re.search(r"\.(pdf|docx?|xlsx?|pptx?)(\?|$)", absu, re.I))
        if not (other == host or other.endswith("." + host) or is_doc or "q4cdn" in other or "maya" in other):
            continue
        if len(text) < 6 or len(text) > 300 or absu in seen:
            continue
        if not (is_doc or REPORTY.search(text) or REPORTY.search(absu)):
            continue
        seen.add(absu)
        out.append((absu, text))
    return out


def add(market: str, ticker: str, url: str) -> tuple[int, str | None]:
    """Start watching a page: record what is on it now. Returns (report links found, error)."""
    url = url.strip()
    if not re.match(r"^https?://[^\s/]+\.[^\s]+", url):
        return 0, "Enter the full address, starting with https://"
    try:
        found = links(url)
    except requests.RequestException as exc:
        return 0, f"The page couldn't be opened ({exc.__class__.__name__})."
    data = _all()
    data[f"{market}:{ticker}"] = {"url": url, "seen": [_h(u) for u, _ in found][:MAX_SEEN],
                                  "checked": datetime.now(timezone.utc).isoformat(), "links": len(found), "error": ""}
    _save(data)
    return len(found), None


def remove(market: str, ticker: str) -> None:
    data = _all()
    data.pop(f"{market}:{ticker}", None)
    _save(data)


def check(market: str, ticker: str) -> int:
    """Store links that weren't on the page before as new filings. Returns how many."""
    data = _all()
    key = f"{market}:{ticker}"
    entry = data.get(key)
    if not entry:
        return 0
    try:
        found = links(entry["url"])
        entry["error"] = ""
    except requests.RequestException as exc:
        entry["error"] = f"{exc.__class__.__name__}"
        entry["checked"] = datetime.now(timezone.utc).isoformat()
        data[key] = entry
        _save(data)
        return 0
    seen = set(entry.get("seen") or [])
    fresh = [(u, t) for u, t in found if _h(u) not in seen][:MAX_NEW]
    stored = _store(market, ticker, fresh) if fresh else 0
    entry["seen"] = ([_h(u) for u, _ in fresh] + list(entry.get("seen") or []))[:MAX_SEEN]
    entry["links"] = len(found)
    entry["checked"] = datetime.now(timezone.utc).isoformat()
    data[key] = entry
    _save(data)
    return stored


def _store(market: str, ticker: str, items: list[tuple[str, str]]) -> int:
    from sources import get_source
    from .translate import looks_english, translate_lines
    src = get_source(market)
    lang = (src.source_lang if src and src.source_lang not in ("en", "auto") else "iw") if market == "IL" else \
        (src.source_lang if src else "en")
    with get_engine().connect() as conn:
        comp = conn.execute(select(companies.c.name_en, companies.c.name_local)
                            .where(companies.c.market == market, companies.c.ticker == ticker)).first()
    name_en = comp[0] if comp else ticker
    todo = [t for _, t in items if not looks_english(t)]
    english = dict(zip(todo, translate_lines(todo, lang))) if todo else {}
    today = src.today() if src else datetime.now(timezone.utc).date()
    n = 0
    for url, text in items:
        row = dict(uid=f"{market}:WEB:{_h(url)}", market=market, ticker=ticker, company_name=name_en,
                   filed_date=today, title_local=text, title_en=english.get(text) or text,
                   filer="Company website", url=url, notified=False, created_at=utcnow())
        try:
            with get_engine().begin() as conn:
                conn.execute(insert(filings).values(**row))
            n += 1
        except IntegrityError:
            pass
    return n


def refresh() -> int:
    """Worker step: check every watched investor page."""
    total = 0
    for key in list(_all()):
        market, ticker = key.split(":", 1)
        try:
            total += check(market, ticker)
        except Exception as exc:
            log.warning("investor page check failed for %s: %s", key, exc)
    return total
