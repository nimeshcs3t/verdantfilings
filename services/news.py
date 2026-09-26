"""Company news from Google News RSS (free, no key)."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import requests

from .translate import translate_lines

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FilingsDesk/1.0)"}


def google_news(query: str, hl: str = "en-US", gl: str = "US", ceid: str = "US:en", limit: int = 10) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return []
    items = []
    for e in feedparser.parse(r.content).entries[:limit]:
        source = (e.get("source") or {}).get("title", "")
        title = e.get("title", "")
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc) if e.get("published_parsed") else None
        items.append({"title": title, "link": e.get("link", ""), "source": source, "published": published})
    return items


def english_news(name_en: str, ticker: str, limit: int = 10) -> list[dict]:
    return google_news(f'"{name_en}" when:30d', limit=limit)


def local_news(name_local: str, params: dict, src_lang: str, limit: int = 10) -> list[dict]:
    items = google_news(f'"{name_local}" when:14d', limit=limit, **params)
    if items and src_lang != "en":
        english = translate_lines([i["title"] for i in items], src_lang)
        for item, en in zip(items, english):
            item["title_local"], item["title"] = item["title"], en
    return items
