"""Connections between companies: peers a member picks (with suggestions for US stocks), and filings that name
another followed company (contracts, supply deals, takeovers). No AI: names are matched in titles, overviews and
stored filing text."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import and_, delete, insert, or_, select

from core.db import companies, company_peers, filing_mentions, filing_texts, filings, get_engine, us_stocks, utcnow

log = logging.getLogger(__name__)
LEGAL = r"(?:,?\s+(?:co\.?,?\s*ltd\.?|company,?\s+limited|co\.|corp\.?|corporation|inc\.?|incorporated|ltd\.?|limited|plc|"\
        r"s\.?a\.?|n\.?v\.?|ag|se|asa|ab|oyj|a/s|spa|s\.p\.a\.|k\.k\.|holdings?|group|\(the\)))+\s*$"
GENERIC = {"first", "united", "global", "general", "national", "american", "international", "capital", "energy", "royal",
           "china", "korea", "japan", "new", "great", "pacific", "north", "south", "east", "west", "central"}


def plain_name(name: str) -> str:
    """'Samsung Electronics Co., Ltd.' -> 'Samsung Electronics'; 'NVIDIA Corporation' -> 'NVIDIA'."""
    n = " ".join((name or "").split())
    prev = None
    while prev != n:
        prev, n = n, re.sub(LEGAL, "", n, flags=re.I).strip(" ,.")
    return n


class NameMatcher:
    """Finds followed companies named in a piece of text. Three combined patterns, so checking hundreds of names
    against a filing is one pass: English names (whole words, case as written; all-caps names any case) and
    Korean/Japanese/Chinese names (no word boundaries in those scripts)."""

    def __init__(self):
        with get_engine().connect() as conn:
            rows = conn.execute(select(companies.c.market, companies.c.ticker, companies.c.name_en, companies.c.name_local)).all()
        self.by_name: dict[str, set[tuple[str, str]]] = {}
        self.written: dict[str, set[str]] = {}      # lower-case English name -> the ways it's written
        sensitive, insensitive, local = set(), set(), set()
        for market, ticker, name_en, name_local in rows:
            en = plain_name(name_en or "")
            if len(en) >= 4 and en.lower() not in GENERIC:
                (insensitive if en.isupper() and len(en) > 4 else sensitive).add(en)
                self.by_name.setdefault(en.lower(), set()).add((market, ticker))
                self.written.setdefault(en.lower(), set()).add(en)
            loc = plain_name(name_local or "")
            if loc and loc != en and len(loc) >= 3 and not loc.isascii():
                local.add(loc)
                self.by_name.setdefault(loc, set()).add((market, ticker))
        alt = lambda names: "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        self.res = []
        if sensitive:                       # as written, or in capitals ('SAMSUNG ELECTRONICS'), never lower-case
            self.res.append((re.compile(rf"(?<![\w-])(?:{alt(sensitive)})(?![\w-])", re.I), "cased"))
        if insensitive:                     # all-caps names ('NVIDIA') in any case
            self.res.append((re.compile(rf"(?<![\w-])(?:{alt(insensitive)})(?![\w-])", re.I), "any"))
        if local:
            self.res.append((re.compile(alt(local)), "local"))

    def __bool__(self) -> bool:
        return bool(self.res)

    def find(self, text: str, exclude: tuple[str, str] | None = None) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        for rx, kind in self.res:
            for m in rx.finditer(text or ""):
                hit = m.group(0)
                if kind == "local":
                    found |= self.by_name.get(hit, set())
                    continue
                if kind == "cased" and not (hit in self.written.get(hit.lower(), ()) or hit.isupper()):
                    continue
                found |= self.by_name.get(hit.lower(), set())
        found.discard(exclude)
        return found


def scan_mentions(days: int | None = 3, batch: int = 2000) -> int:
    """Look for followed companies named in stored filings: the last `days` days, or all stored filings when days
    is None. Returns new links found. The worker runs it every 30 minutes, and once over everything."""
    from core.usage import set_state
    matcher = NameMatcher()
    if not matcher:
        return 0
    added, last_uid = 0, ""
    while True:
        with get_engine().connect() as conn:
            q = (select(filings.c.uid, filings.c.market, filings.c.ticker, filings.c.title_en, filings.c.title_local,
                        filings.c.summary_en, filing_texts.c.body)
                 .select_from(filings.outerjoin(filing_texts, filing_texts.c.uid == filings.c.uid))
                 .where(filings.c.uid > last_uid).order_by(filings.c.uid).limit(batch))
            if days is not None:
                q = q.where(filings.c.created_at >= utcnow() - timedelta(days=days))
            rows = conn.execute(q).mappings().all()
            if not rows:
                break
            known = set(conn.execute(select(filing_mentions.c.uid, filing_mentions.c.market, filing_mentions.c.ticker)
                                     .where(filing_mentions.c.uid.in_([r["uid"] for r in rows]))).all())
        found = []
        for r in rows:
            text = " ".join(str(r.get(k) or "") for k in ("title_en", "title_local", "summary_en")) + " " + str(r.get("body") or "")[:30000]
            for market, ticker in matcher.find(text, (r["market"], r["ticker"])):
                if (r["uid"], market, ticker) not in known:
                    found.append({"uid": r["uid"], "market": market, "ticker": ticker, "found_at": utcnow()})
                    known.add((r["uid"], market, ticker))
        if found:
            with get_engine().begin() as conn:
                conn.execute(insert(filing_mentions), found)
            added += len(found)
        last_uid = rows[-1]["uid"]
        if len(rows) < batch:
            break
    set_state("mentions_scanned_until", utcnow().isoformat())
    if days is None:
        set_state("mentions_full_scan", utcnow().isoformat())
    return added


def scan_mentions_auto() -> int:
    """Worker step: the first time over all stored filings, then the last 3 days on each run."""
    from core.usage import get_state
    return scan_mentions(None if not get_state("mentions_full_scan") else 3)


def scan_feed(market: str, feed: list[dict], matcher: "NameMatcher | None" = None) -> list[tuple[dict, set]]:
    """Market-wide announcements whose title names a followed company (another company's filing)."""
    matcher = matcher or NameMatcher()
    if not matcher:
        return []
    out = []
    for f in feed:
        hits = matcher.find(f"{f.get('title_en') or ''} | {f.get('title_local') or ''}", (market, f.get("ticker") or ""))
        if hits:
            out.append((f, hits))
    return out


def mentions_of(market: str, ticker: str, limit: int = 30) -> list[dict]:
    """Other companies' stored filings that name this company."""
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(
            select(filings).join(filing_mentions, filing_mentions.c.uid == filings.c.uid)
            .where(filing_mentions.c.market == market, filing_mentions.c.ticker == ticker)
            .order_by(filings.c.filed_date.desc()).limit(limit)).mappings()]


def market_mentions_of(market: str, ticker: str, limit: int = 30) -> list[dict]:
    """Announcements from anywhere on the covered markets (not only followed companies) that name this company."""
    from core.db import market_events
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(
            select(market_events).join(filing_mentions, filing_mentions.c.uid == market_events.c.uid)
            .where(filing_mentions.c.market == market, filing_mentions.c.ticker == ticker,
                   market_events.c.uid.not_in(select(filings.c.uid)))
            .order_by(market_events.c.filed_date.desc()).limit(limit)).mappings()]


def mentioned_by(market: str, ticker: str, limit: int = 30) -> list[dict]:
    """This company's filings that name another followed company, with who was named."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(filings, filing_mentions.c.market.label("m_market"), filing_mentions.c.ticker.label("m_ticker"),
                   companies.c.name_en.label("m_name"))
            .join(filing_mentions, filing_mentions.c.uid == filings.c.uid)
            .join(companies, and_(companies.c.market == filing_mentions.c.market, companies.c.ticker == filing_mentions.c.ticker))
            .where(filings.c.market == market, filings.c.ticker == ticker)
            .order_by(filings.c.filed_date.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


# ---- peers ---------------------------------------------------------------------------------------------------------
def peers(user_id: int, market: str, ticker: str) -> list[tuple[str, str]]:
    with get_engine().connect() as conn:
        return [tuple(r) for r in conn.execute(select(company_peers.c.peer_market, company_peers.c.peer_ticker).where(
            company_peers.c.user_id == user_id, company_peers.c.market == market, company_peers.c.ticker == ticker)
            .order_by(company_peers.c.added_at))]


def add_peer(user_id: int, market: str, ticker: str, peer_market: str, peer_ticker: str) -> str | None:
    """Adds the peer (and starts collecting its filings, like any company that's been looked up)."""
    from .pipeline import get_company
    if (market, ticker) == (peer_market, peer_ticker):
        return "That's the same company."
    comp = get_company(peer_market, peer_ticker)
    if not comp:
        return "Couldn't find that company."
    if len(peers(user_id, market, ticker)) >= 12:
        return "Up to 12 peers per company."
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(company_peers).values(user_id=user_id, market=market, ticker=ticker,
                                                      peer_market=peer_market, peer_ticker=comp["ticker"], added_at=utcnow()))
    except Exception:
        return "Already a peer."
    return None


def remove_peer(user_id: int, market: str, ticker: str, peer_market: str, peer_ticker: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(company_peers).where(company_peers.c.user_id == user_id, company_peers.c.market == market,
                                                 company_peers.c.ticker == ticker, company_peers.c.peer_market == peer_market,
                                                 company_peers.c.peer_ticker == peer_ticker))


def suggestions(market: str, ticker: str, limit: int = 6) -> list[dict]:
    """US stocks: same industry, closest in size."""
    if market != "US":
        return []
    with get_engine().connect() as conn:
        me = conn.execute(select(us_stocks).where(us_stocks.c.ticker == ticker)).mappings().first()
        if not me or not me["industry"]:
            return []
        rows = [dict(r) for r in conn.execute(select(us_stocks).where(
            us_stocks.c.industry == me["industry"], us_stocks.c.ticker != ticker, us_stocks.c.market_cap > 0)).mappings()]
    cap = me["market_cap"] or 0
    import math
    rows.sort(key=lambda r: abs(math.log((r["market_cap"] or 1) / (cap or 1))) if cap else -(r["market_cap"] or 0))
    return rows[:limit]


def peer_table(user_id: int, market: str, ticker: str) -> list[dict]:
    """The company and its peers side by side: price, 1-year return, P/E, P/S, growth, margins."""
    from . import portfolio, prices, valuation
    from .pipeline import get_company
    from sources import get_source
    pairs = [(market, ticker)] + peers(user_id, market, ticker)
    us = {}
    us_tickers = [t for m, t in pairs if m == "US"]
    if us_tickers:
        with get_engine().connect() as conn:
            us = {r["ticker"]: dict(r) for r in conn.execute(select(us_stocks).where(us_stocks.c.ticker.in_(us_tickers))).mappings()}
    out = []
    for m, t in pairs:
        comp = get_company(m, t, resolve=False) or {}
        try:
            hist = prices.history(m, t, years=1)
        except Exception:
            hist = []
        cur = portfolio.currency_for(m, t)
        try:
            q = valuation.quick_view(m, t, hist, cur)
        except Exception:
            q = {}
        u = us.get(t) if m == "US" else None
        if u:          # fill gaps from the US market data
            from .us_screener import ratios
            for k, v in ratios(u).items():
                if q.get(k) is None:
                    q[k] = v
        src = get_source(m)
        out.append({"market": m, "ticker": t, "name": comp.get("name_en") or t, "country": src.country if src else m,
                    "self": (m, t) == (market, ticker), "price": q.get("price"), "currency": cur, "ret1y": q.get("ret1y"),
                    "market_cap": q.get("market_cap"), "pe": q.get("pe"), "ps": q.get("ps"), "growth": q.get("growth"),
                    "op_margin": q.get("op_margin"), "net_margin": q.get("net_margin")})
    return out


def peer_filings(user_id: int, market: str, ticker: str, limit: int = 20) -> list[dict]:
    pairs = peers(user_id, market, ticker)
    if not pairs:
        return []
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(filings).where(or_(*[and_(filings.c.market == m, filings.c.ticker == t)
                                                                          for m, t in pairs]))
                                              .order_by(filings.c.filed_date.desc()).limit(limit)).mappings()]
