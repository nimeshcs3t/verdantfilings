"""Substack posts about a company: Substack's own search, plus the newsletters a member follows (their public RSS
feeds). No AI and no keys. Posts are kept only if the title or summary names the company or its ticker."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import requests

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0.0.0 Safari/537.36", "Accept": "application/json, text/plain, */*"}
NAME_STOP = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "holdings", "holding",
             "group", "sa", "se", "nv", "ag", "asa", "ab", "spa", "the", "class", "a", "b", "technologies", "technology",
             "industries", "international", "electronics", "systems", "therapeutics"}


def short_name(name: str) -> str:
    """'Samsung Electronics Co., Ltd.' -> 'Samsung Electronics'; 'NVIDIA Corporation' -> 'NVIDIA'."""
    words = re.sub(r"[^\w&.\- ]", " ", name or "").split()
    while words and words[0].lower() == "the":
        words = words[1:]
    keep = []
    for w in words:
        if w.lower().strip(".") in NAME_STOP and keep:
            break
        keep.append(w.strip(".,"))
    return " ".join(keep[:3]) or (name or "")


def _ticker_ok(ticker: str) -> bool:
    """Tickers worth matching as words: letters, 2 to 6 long ('NVDA', 'BHP'), not numbers like '005930'."""
    return bool(re.fullmatch(r"[A-Z]{2,6}", ticker or ""))


def matcher(name: str, ticker: str):
    """A test for whether a post's text is about this company."""
    sn = short_name(name)
    pats = []

    def word(w: str):                       # 'NVIDIA' also matches 'Nvidia'; 'Meta' must be capitalised as written
        return re.compile(rf"(?<![\w-]){re.escape(w)}(?![\w-])", re.I if w.isupper() and len(w) > 3 else 0)
    if len(sn) >= 3:
        pats.append(word(sn))
        first = sn.split()[0]
        if len(first) >= 4 and first.lower() not in {"china", "korea", "japan", "first", "united", "global", "great",
                                                      "south", "north", "american", "new", "royal", "national"}:
            pats.append(word(first))
    if _ticker_ok(ticker):                  # '$NVDA' or '(NVDA)' always; the bare word only for 3+ letters
        bare = rf"|\b{re.escape(ticker)}\b" if len(ticker) >= 3 else ""
        pats.append(re.compile(rf"\${re.escape(ticker)}\b|\({re.escape(ticker)}\){bare}"))
    return lambda text: any(p.search(text or "") for p in pats)


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _walk_posts(node, out: list) -> None:
    """Find post objects anywhere in Substack's search reply (they sit at different depths by result type)."""
    if isinstance(node, dict):
        if node.get("canonical_url") and node.get("title") and (node.get("post_date") or node.get("type") == "newsletter"):
            out.append(node)
            return
        for v in node.values():
            _walk_posts(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_posts(v, out)


def _publication(post: dict) -> str:
    pub = post.get("publication") or {}
    if isinstance(pub, dict) and pub.get("name"):
        return pub["name"]
    bylines = post.get("publishedBylines") or []
    if bylines and isinstance(bylines[0], dict):
        return bylines[0].get("name") or ""
    m = re.match(r"https?://([^./]+)\.substack\.com", post.get("canonical_url", ""))
    return m.group(1) if m else ""


def search(name: str, ticker: str, days: int = 90, limit: int = 12, report: list | None = None) -> list[dict]:
    """Substack's site search for the company name (and the ticker when it's distinctive). If `report` is a list, one
    entry per query is added to it: what Substack answered and what was kept, for the Admin check."""
    is_about = matcher(name, ticker)
    queries = [short_name(name)] + ([f"${ticker}"] if _ticker_ok(ticker) else [])
    since = datetime.now(timezone.utc) - timedelta(days=days)
    posts, seen = [], set()
    for q in queries:
        if not q or len(q) < 2:
            continue
        entry = {"query": q, "status": None, "returned": 0, "kept": 0, "too_old": 0, "off_topic": []}
        try:
            r = requests.get(f"https://substack.com/api/v1/top/search?query={quote_plus(q)}&page=0", headers=HEADERS, timeout=15)
            entry["status"] = r.status_code
            r.raise_for_status()
            found: list = []
            _walk_posts(r.json(), found)
        except Exception as exc:
            entry["error"] = str(exc)[:160]
            log.info("Substack search failed for %s: %s", q, exc)
            if report is not None:
                report.append(entry)
            continue
        entry["returned"] = len(found)
        for p in found:
            url = p["canonical_url"]
            when = _when(p.get("post_date"))
            text = f"{p.get('title') or ''} {p.get('subtitle') or ''} {p.get('description') or ''} {p.get('truncated_body_text') or ''}"
            if url in seen:
                continue
            if when and when < since:
                entry["too_old"] += 1
                continue
            if not is_about(text):
                entry["off_topic"].append(p.get("title") or "")
                continue
            seen.add(url)
            entry["kept"] += 1
            posts.append({"title": p["title"].strip(), "summary": (p.get("subtitle") or p.get("description") or "").strip()[:240],
                          "link": url, "source": _publication(p), "published": when,
                          "paid": p.get("audience") in ("only_paid", "founding"), "from": "search"})
        if report is not None:
            report.append(entry)
    return sorted(posts, key=lambda p: p["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]


def feed_url(newsletter: str) -> str | None:
    """'thegeneralist', 'thegeneralist.substack.com', 'https://www.example.com' (custom domain) -> its RSS feed."""
    s = (newsletter or "").strip().rstrip("/")
    if not s or re.search(r"\s|[^\w.\-/:~%?=&]", s):
        return None
    host = re.sub(r"^https?://", "", s).split("/")[0]
    if "." not in host and not re.fullmatch(r"[a-z0-9\-]{2,63}", s.lower()):
        return None
    if s.endswith("/feed"):
        return s if s.startswith("http") else f"https://{s}"
    if re.fullmatch(r"[a-z0-9\-]{2,63}", s.lower()):
        return f"https://{s.lower()}.substack.com/feed"
    if not s.startswith("http"):
        s = "https://" + s
    return s + "/feed"


_feed_cache: dict[str, tuple[float, str, list]] = {}      # feed url -> (fetched at, newsletter title, entries)
FEED_TTL = 3 * 3600


def _feed(url: str) -> tuple[str, list]:
    """One newsletter's recent posts, downloaded at most once per 3 hours and shared by every company page."""
    import time
    hit = _feed_cache.get(url)
    if hit and time.time() - hit[0] < FEED_TTL:
        return hit[1], hit[2]
    try:
        r = requests.get(url, headers={**HEADERS, "Accept": "application/rss+xml, application/xml"}, timeout=15)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as exc:
        log.info("Substack feed %s failed: %s", url, exc)
        return (hit[1], hit[2]) if hit else ("", [])
    entries = []
    for e in feed.entries[:40]:
        when = datetime(*e.published_parsed[:6], tzinfo=timezone.utc) if e.get("published_parsed") else None
        body = re.sub(r"<[^>]+>", " ", e["content"][0].get("value", "")) if e.get("content") else ""
        summary = " ".join(re.sub(r"<[^>]+>", " ", e.get("summary", "") or "").split())
        entries.append({"title": e.get("title", "").strip(), "summary": summary, "body": body[:20000],
                        "link": e.get("link", ""), "published": when})
    title = (feed.feed or {}).get("title") or ""
    _feed_cache[url] = (time.time(), title, entries)
    return title, entries


def from_feeds(newsletters: list[str], name: str, ticker: str, days: int = 120, limit: int = 12) -> list[dict]:
    """Posts about this company from the newsletters the member follows."""
    from concurrent.futures import ThreadPoolExecutor
    is_about = matcher(name, ticker)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    urls = [(n, feed_url(n)) for n in newsletters[:30]]
    urls = [(n, u) for n, u in urls if u]
    with ThreadPoolExecutor(max_workers=8) as pool:
        feeds = list(pool.map(lambda nu: (nu[0], *_feed(nu[1])), urls))
    posts = []
    for n, title, entries in feeds:
        for e in entries:
            if (e["published"] and e["published"] < since) or not is_about(f"{e['title']} {e['summary']} {e['body']}"):
                continue
            posts.append({"title": e["title"], "summary": e["summary"][:240], "link": e["link"], "source": title or n,
                          "published": e["published"], "paid": False, "from": "followed"})
    return sorted(posts, key=lambda p: p["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]


def posts_for(name: str, ticker: str, newsletters: list[str] | None = None) -> list[dict]:
    """Followed newsletters first, then Substack-wide search, without duplicates."""
    return lookup(name, ticker, newsletters)["posts"]


def lookup(name: str, ticker: str, newsletters: list[str] | None = None) -> dict:
    """Posts plus whether Substack's search could be reached, so the page can say why it's empty."""
    mine = from_feeds(newsletters or [], name, ticker) if newsletters else []
    links = {p["link"] for p in mine}
    report: list = []
    found = search(name, ticker, report=report)
    reached = any(e.get("status") == 200 for e in report)
    return {"posts": mine + [p for p in found if p["link"] not in links], "search_reached": reached,
            "search_error": next((e.get("error") or f"status {e.get('status')}" for e in report if e.get("status") != 200), None),
            "followed": len(newsletters or [])}


def diagnose(name: str, ticker: str, newsletters: list[str] | None = None) -> dict:
    """Everything the Admin 'Substack check' shows: per search query and per followed newsletter."""
    report: list = []
    search(name, ticker, report=report)
    feeds = []
    for n in (newsletters or [])[:30]:
        url = feed_url(n)
        if not url:
            feeds.append({"newsletter": n, "url": None, "status": "not a valid newsletter name", "posts": 0, "matching": 0})
            continue
        _feed_cache.pop(url, None)
        title, entries = _feed(url)
        is_about = matcher(name, ticker)
        feeds.append({"newsletter": n, "url": url, "status": "ok" if entries else "no posts read (check the name)",
                      "title": title, "posts": len(entries),
                      "matching": sum(1 for e in entries if is_about(f"{e['title']} {e['summary']} {e['body']}"))})
    return {"short_name": short_name(name), "ticker_used": _ticker_ok(ticker), "search": report, "feeds": feeds}
