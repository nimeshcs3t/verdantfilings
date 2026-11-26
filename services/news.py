"""Company news from Google News RSS (free, no key)."""
from __future__ import annotations

import re

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
    for e in feedparser.parse(r.content).entries:
        source = (e.get("source") or {}).get("title", "")
        title = e.get("title", "")
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc) if e.get("published_parsed") else None
        items.append({"title": title, "link": e.get("link", ""), "source": source, "published": published})
    # Google returns its "most relevant" order; show the newest first so a fresh release isn't cut off
    items.sort(key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:limit]


LEGAL_WORDS = {"oyj", "oy", "abp", "ab", "publ", "asa", "as", "a/s", "plc", "ag", "se", "sa", "nv", "spa", "srl", "ltd",
               "limited", "inc", "corp", "corporation", "co", "company", "holding", "holdings", "group", "gmbh", "kgaa",
               "bhd", "tbk", "pcl", "kk"}


def short_name(name: str) -> str:
    """'Sensofusion Oyj' -> 'Sensofusion', 'Samsung Electronics Co., Ltd.' -> 'Samsung Electronics': headlines rarely
    carry the legal form. Only trailing legal words are removed, and at least one word is always kept."""
    words = re.sub(r"\(.*?\)", " ", name or "").split()
    while len(words) > 1 and re.sub(r"[.,]", "", words[-1]).lower() in LEGAL_WORDS:
        words.pop()
    return " ".join(words).strip(" ,") or (name or "").strip()


def english_news(name_en: str, ticker: str, limit: int = 10) -> list[dict]:
    return google_news(f'"{short_name(name_en)}" when:30d', limit=limit)


def local_news(name_local: str, params: dict, src_lang: str, limit: int = 10) -> list[dict]:
    items = google_news(f'"{short_name(name_local)}" when:14d', limit=limit, **params)
    if items and src_lang != "en":
        english = translate_lines([i["title"] for i in items], src_lang)
        for item, en in zip(items, english):
            item["title_local"], item["title"] = item["title"], en
    return items


# ---- more sources --------------------------------------------------------------------------------------------
def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:90]


def merge(*lists: list[dict], limit: int = 20) -> list[dict]:
    """Several feeds into one list, newest first, with the same story from two sources shown once."""
    seen, out = set(), []
    for item in sorted((i for lst in lists for i in lst),
                       key=lambda i: i.get("published") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        key = _norm(item.get("title"))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:limit]


def rss(url: str, source: str = "", limit: int = 20) -> list[dict]:
    """Any RSS or Atom feed as news items."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return []
    feed = feedparser.parse(r.content)
    name = source or (feed.feed or {}).get("title", "")
    items = []
    for e in feed.entries[:limit]:
        when = e.get("published_parsed") or e.get("updated_parsed")
        items.append({"title": " ".join((e.get("title") or "").split()), "link": e.get("link", ""), "source": name,
                      "published": datetime(*when[:6], tzinfo=timezone.utc) if when else None})
    return items


def yahoo_news(market: str, ticker: str, limit: int = 15) -> list[dict]:
    """Yahoo Finance's headline feed for one share (wire stories, press releases, analysis)."""
    from .prices import SUFFIX
    sym = ticker if market == "US" else f"{ticker}{(SUFFIX.get(market) or [''])[0]}"
    if market not in SUFFIX or not re.fullmatch(r"[A-Za-z0-9.\-]{1,15}", sym):
        return []
    items = rss(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(sym)}&region=US&lang=en-US",
                "Yahoo Finance", limit)
    for i in items:
        i["source"] = "Yahoo Finance"
    return items


def company_news(name_en: str, ticker: str, market: str = "", limit: int = 15) -> list[dict]:
    """Google News plus Yahoo Finance for one company, merged."""
    return merge(english_news(name_en, ticker, limit), yahoo_news(market, ticker) if market else [], limit=limit)


# Defence industry news by region. Queries name the region's listed defence companies plus the trade press, so
# Korean, Israeli and European names show up, not only US primes.
DEFENCE = {
    "US": ['(Lockheed OR Northrop OR "General Dynamics" OR RTX OR "L3Harris" OR Anduril OR Palantir OR Kratos OR '
           '"AeroVironment") (Pentagon OR contract OR defense) when:7d'],
    "Europe": ['(Rheinmetall OR Hensoldt OR Renk OR Saab OR Leonardo OR Thales OR "BAE Systems" OR Kongsberg OR KNDS OR '
               'Safran OR "Dassault Aviation" OR Indra OR Nammo OR Patria OR CSG OR "Theon International") '
               '(defence OR defense OR order OR contract) when:7d',
               '(NATO OR "European defence" OR "ReArm Europe" OR "European Defence Fund") (order OR procurement OR spending) when:7d'],
    "Israel": ['("Elbit Systems" OR Rafael OR "Israel Aerospace" OR IAI OR "Smart Shooter" OR NextVision OR Aryt OR '
               '"TAT Technologies" OR "Bet Shemesh Engines" OR "Arbe Robotics" OR "Israel defense exports") when:7d'],
    "Korea": ['("Hanwha Aerospace" OR "LIG Nex1" OR "Korea Aerospace Industries" OR "Hyundai Rotem" OR "Hanwha Ocean" OR '
              '"Poongsan" OR "K2 tank" OR "K9 howitzer" OR "KF-21" OR "Korean defense") when:7d'],
}
DEFENCE_LOCAL = {"Korea": ('방산 수주 OR 한화에어로스페이스 OR LIG넥스원 OR 현대로템 when:3d', {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}, "ko"),
                 "Israel": ('אלביט OR רפאל OR "התעשייה האווירית" OR "סמארט שוטר" when:3d', {"hl": "he", "gl": "IL", "ceid": "IL:he"}, "iw")}
DEFENCE_PRESS = ["https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "https://breakingdefense.com/feed/",
                 "https://www.thedefensepost.com/feed/", "https://www.defense-aerospace.com/feed/"]


def defence_news(region: str = "All", limit: int = 40) -> list[dict]:
    regions = list(DEFENCE) if region == "All" else [region]
    lists = []
    for reg in regions:
        for q in DEFENCE.get(reg, []):
            items = google_news(q, limit=25)
            for i in items:
                i["region"] = reg
            lists.append(items)
        if reg in DEFENCE_LOCAL:
            q, params, lang = DEFENCE_LOCAL[reg]
            local = google_news(q, limit=12, **params)
            if local:
                english = translate_lines([i["title"] for i in local], lang)
                for i, en in zip(local, english):
                    i["title_local"], i["title"], i["region"] = i["title"], en or i["title"], reg
            lists.append(local)
    if region in ("All", "US"):
        for url in DEFENCE_PRESS:
            press = rss(url, limit=15)
            for i in press:
                i["region"] = "Press"
            lists.append(press)
    return merge(*lists, limit=limit)


MARKETS = {"US": "(stocks OR \"Wall Street\" OR \"S&P 500\" OR Nasdaq) when:1d",
           "Europe": "(\"European shares\" OR STOXX OR DAX OR FTSE) when:1d",
           "Korea": "(KOSPI OR KOSDAQ OR \"Korean stocks\") when:1d",
           "Israel": "(\"Tel Aviv Stock Exchange\" OR TA-35 OR \"Israeli stocks\" OR shekel) when:2d",
           "Japan": "(Nikkei OR Topix OR \"Japanese stocks\") when:1d",
           "Australia": "(ASX OR \"Australian shares\") when:1d"}


def market_news(region: str = "All", limit: int = 40) -> list[dict]:
    regions = list(MARKETS) if region == "All" else [region]
    lists = []
    for reg in regions:
        items = google_news(MARKETS[reg], limit=15)
        for i in items:
            i["region"] = reg
        lists.append(items)
    return merge(*lists, limit=limit)
