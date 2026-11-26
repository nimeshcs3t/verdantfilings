"""Free sell-side research: the public research and insights pages of the big banks and brokers, with each one's
latest pieces where they can be found (the site's own RSS feed if it has one, else Google News for that site)."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

from .news import HEADERS, google_news, merge, rss

# (group, name, research page, Google News site filter)
BANKS = [
    ("Bulge bracket", "Goldman Sachs", "https://www.goldmansachs.com/insights/reports", "goldmansachs.com/insights"),
    ("Bulge bracket", "J.P. Morgan", "https://www.jpmorgan.com/insights/global-research", "jpmorgan.com/insights"),
    ("Bulge bracket", "Morgan Stanley", "https://www.morganstanley.com/insights", "morganstanley.com/insights"),
    ("Bulge bracket", "BofA", "https://business.bofa.com/en-us/content/market-strategies-insights.html", "business.bofa.com"),
    ("Bulge bracket", "Citi", "https://www.citigroup.com/global/insights/tags/citi-research", "citigroup.com/global/insights"),
    ("Bulge bracket", "UBS", "https://www.ubs.com/global/en/investment-bank/insights-and-data.html", "ubs.com/global/en/investment-bank"),
    ("Bulge bracket", "Barclays", "https://www.ib.barclays/our-insights.html", "ib.barclays"),
    ("Bulge bracket", "Deutsche Bank", "https://www.dbresearch.com", "dbresearch.com"),
    ("Bulge bracket", "HSBC", "https://www.business.hsbc.com/en-gb/products/global-investment-research", "business.hsbc.com"),
    ("Rest of the street", "Aurelion Research", "https://read.aurelionresearch.com", "aurelionresearch.com"),
    ("Rest of the street", "Jefferies", "https://www.jefferies.com/insights/", "jefferies.com/insights"),
    ("Rest of the street", "Nomura", "https://www.nomuraholdings.com/en/services/global_research.html", "nomuraholdings.com"),
    ("Rest of the street", "Société Générale", "https://insight-public.sgmarkets.com", "insight-public.sgmarkets.com"),
    ("Rest of the street", "BNP Paribas Markets 360", "https://globalmarkets.cib.bnpparibas/markets-360/", "globalmarkets.cib.bnpparibas"),
    ("Rest of the street", "RBC Capital Markets", "https://www.rbccm.com/en/insights.page", "rbccm.com/en/insights"),
    ("Rest of the street", "Wells Fargo", "https://www.wellsfargoadvisors.com/research-analysis.htm", "wellsfargoadvisors.com"),
]


def _site_feeds(page: str) -> list[str]:
    """RSS or Atom feeds the research page announces in its <head>, plus /feed for newsletter-style sites."""
    feeds = []
    try:
        r = requests.get(page, headers={**HEADERS, "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"},
                         timeout=15)
        if r.ok:
            for tag in re.findall(r"<link[^>]+>", r.text[:200000], re.I):
                if re.search(r"application/(rss|atom)\+xml", tag, re.I):
                    m = re.search(r'href=["\']([^"\']+)', tag)
                    if m:
                        feeds.append(urljoin(page, m.group(1)))
    except requests.RequestException:
        pass
    if "aurelionresearch" in page:
        feeds.append(page.rstrip("/") + "/feed")
    return list(dict.fromkeys(feeds))[:2]


def latest(name: str, page: str, site: str, limit: int = 6) -> list[dict]:
    lists = [rss(f, name, limit=15) for f in _site_feeds(page)]
    lists.append(google_news(f"site:{site} when:120d", limit=12))
    items = merge(*lists, limit=limit)
    for i in items:
        i["source"] = i.get("source") or name
    return items


def all_latest(limit: int = 6) -> dict[str, list[dict]]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda b: latest(b[1], b[2], b[3], limit), BANKS))
    return {b[1]: r for b, r in zip(BANKS, results)}
