"""Foreign companies traded over the counter in the US (ADRs and F-shares such as KWHIY, QNTQY, TAKOF) file in their
home market, not with the SEC. This finds the home listing on a market the app reads, so following the US symbol
can show the real filings. Uses Yahoo Finance names and symbol search; no AI."""
from __future__ import annotations

import re
from functools import lru_cache

# Yahoo Finance exchange code -> (app market, symbol suffix to strip)
YAHOO_EXCHANGE = {
    "JPX": ("JP", ".T"), "KSC": ("KR", ".KS"), "KOE": ("KR", ".KQ"), "HKG": ("HK", ".HK"), "TAI": ("TW", ".TW"),
    "TWO": ("TW", ".TWO"), "LSE": ("UK", ".L"), "IOB": ("UK", ".IL"), "PAR": ("FR", ".PA"), "AMS": ("NL", ".AS"),
    "BRU": ("BE", ".BR"), "LIS": ("PT", ".LS"), "ISE": ("IE", ".IR"), "MIL": ("IT", ".MI"), "MCE": ("ES", ".MC"),
    "EBS": ("CH", ".SW"), "GER": ("DE", ".DE"), "FRA": ("DE", ".F"), "VIE": ("AT", ".VI"), "STO": ("SE", ".ST"),
    "CPH": ("DK", ".CO"), "HEL": ("FI", ".HE"), "OSL": ("NO", ".OL"), "ICE": ("IS", ".IC"), "TAL": ("EE", ".TL"),
    "RIS": ("LV", ".RG"), "LIT": ("LT", ".VS"), "ASX": ("AU", ".AX"), "TLV": ("IL", ".TA"), "WSE": ("PL", ".WA"),
}
# preferred when a company is listed in several places (its home exchange first)
PREFERENCE = ["JPX", "KSC", "KOE", "HKG", "TAI", "TWO", "LSE", "PAR", "AMS", "BRU", "LIS", "ISE", "MIL", "MCE", "EBS",
              "GER", "VIE", "STO", "CPH", "HEL", "OSL", "ICE", "TAL", "RIS", "LIT", "ASX", "TLV", "WSE", "FRA", "IOB"]
ADR_WORDS = re.compile(r"\b(SPON(SORED)?|UNSPON(SORED)?|ADR|ADS|EACH|REP(RESENTING)?|ORD(INARY)?|SHS|SHARES|NPV|"
                       r"COMMON|STOCK|COM|CL(ASS)?\s+[A-Z]|DEPOSITARY|RECEIPTS?|LEVEL\s+\w+|\d+(\.\d+)?)\b", re.I)


def plain(name: str) -> str:
    n = ADR_WORDS.sub(" ", re.sub(r"\(.*?\)", " ", name or ""))
    n = re.sub(r"[^\w&' ]", " ", n)
    return " ".join(n.split())


def _words(name: str) -> set[str]:
    stop = {"the", "of", "and", "inc", "ltd", "limited", "co", "corp", "corporation", "plc", "ag", "sa", "nv", "se",
            "group", "holdings", "holding", "company", "kabushiki", "kaisha", "spa", "asa", "ab", "oyj", "a/s"}
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in stop and len(w) > 1}


# links set by hand always win over the automatic search (admins add more on the Admin page)
BUILT_IN = {"AVVSY": ("IT", "AVIO")}
SUFFIX_MARKET = {suffix: market for market, suffix in YAHOO_EXCHANGE.values()}


def parse_target(text: str) -> tuple[str, str] | None:
    """'AVIO.MI' (Yahoo style) or 'IT:AVIO' / 'IT AVIO' -> ('IT', 'AVIO')."""
    t = (text or "").strip().upper()
    m = re.fullmatch(r"([A-Z]{2})[:\s]+([A-Z0-9.\-]+)", t)
    if m:
        return m[1], m[2]
    for suffix in sorted(SUFFIX_MARKET, key=len, reverse=True):
        if t.endswith(suffix.upper()) and len(t) > len(suffix):
            return SUFFIX_MARKET[suffix], t[:-len(suffix)]
    return None


def manual(symbol: str) -> tuple[str, str] | None:
    """A link set by hand (or built in) for this symbol."""
    sym = (symbol or "").strip().upper()
    try:
        from sqlalchemy import select
        from core.db import get_engine, listing_links
        with get_engine().connect() as conn:
            row = conn.execute(select(listing_links.c.market, listing_links.c.ticker)
                               .where(listing_links.c.symbol == sym)).first()
        if row:
            return row[0], row[1]
    except Exception:
        pass
    return BUILT_IN.get(sym)


def links() -> list[dict]:
    from sqlalchemy import select
    from core.db import get_engine, listing_links
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(listing_links).order_by(listing_links.c.symbol)).mappings()]
    have = {r["symbol"] for r in rows}
    return rows + [{"symbol": k, "market": m, "ticker": t, "built_in": True} for k, (m, t) in BUILT_IN.items() if k not in have]


def set_link(symbol: str, target: str, user_id: int | None = None) -> str | None:
    """Link a US / OTC symbol to a home listing. Returns an error message, or None when saved."""
    from sqlalchemy import delete, insert
    from core.db import get_engine, listing_links, utcnow
    from sources import get_source
    from .pipeline import get_company
    sym = re.sub(r"^(OTC|PNK|US)[:\s]+", "", (symbol or "").strip().upper())
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym):
        return "Enter the US or OTC symbol, e.g. AVVSY."
    parsed = parse_target(target)
    if not parsed:
        return "Enter the home listing as a Yahoo symbol (AVIO.MI) or market and ticker (IT:AVIO)."
    market, ticker = parsed
    src = get_source(market)
    if not src or not src.is_configured():
        return f"The app doesn't read market {market} at the moment."
    comp = get_company(market, src.normalize_ticker(ticker) or ticker)
    if not comp:
        return f"Couldn't find {ticker} on {src.country}'s source."
    with get_engine().begin() as conn:
        conn.execute(delete(listing_links).where(listing_links.c.symbol == sym))
        conn.execute(insert(listing_links).values(symbol=sym, market=market, ticker=comp["ticker"], added_by=user_id,
                                                  created_at=utcnow()))
    find.cache_clear()
    return None


def remove_link(symbol: str) -> None:
    from sqlalchemy import delete
    from core.db import get_engine, listing_links
    with get_engine().begin() as conn:
        conn.execute(delete(listing_links).where(listing_links.c.symbol == (symbol or "").upper()))
    find.cache_clear()


def home_of(market: str, ticker: str) -> tuple[str, str, str] | None:
    """The home listing to use instead of a US / OTC / any-market symbol: a hand-set link for any of them, the
    automatic search for OTC lines. (market, ticker, name) or None."""
    if market not in ("US", "OT", "X"):
        return None
    link = manual(ticker)
    if link:
        from sources import get_source
        src = get_source(link[0])
        if src and src.is_configured():
            from .pipeline import get_company
            comp = get_company(link[0], link[1], resolve=True)
            return (link[0], comp["ticker"], comp["name_en"]) if comp else None
        return None
    return find(ticker) if market == "OT" else None


@lru_cache(maxsize=512)
def find(symbol: str, name_hint: str = "") -> tuple[str, str, str] | None:
    """(market, ticker, name) of the home listing of a US OTC symbol, on a market the app has, or None."""
    from sources import configured_sources, get_source
    from .prices import symbol_details, yahoo_search
    link = manual(symbol)
    if link:                                    # set by hand: trust it
        src = get_source(link[0])
        if src and src.is_configured():
            from .pipeline import get_company
            comp = get_company(link[0], link[1])
            if comp:
                return link[0], comp["ticker"], comp["name_en"]
    name = name_hint or (symbol_details(symbol) or {}).get("name") or ""
    query = plain(name)
    if not query:
        return None
    want = _words(query)
    configured = {s.market for s in configured_sources()}
    hits = []
    for q in dict.fromkeys([query, " ".join(query.split()[:2])]):
        for hit in yahoo_search(q, (), 15):
            ex = hit.get("exchange")
            if ex not in YAHOO_EXCHANGE or YAHOO_EXCHANGE[ex][0] not in configured:
                continue
            got = _words(hit.get("longname") or hit.get("shortname") or "")
            if not want or len(want & got) < max(1, min(2, len(want))):
                continue                    # the names must clearly be the same company
            hits.append(hit)
        if hits:
            break
    if not hits:
        return None
    hits.sort(key=lambda h: PREFERENCE.index(h["exchange"]) if h["exchange"] in PREFERENCE else 99)
    hit = hits[0]
    market, suffix = YAHOO_EXCHANGE[hit["exchange"]]
    src = get_source(market)
    ticker = hit["symbol"].removesuffix(suffix)
    ticker = (src.normalize_ticker(ticker) if src else None) or ticker
    return market, ticker, hit.get("longname") or hit.get("shortname") or ticker
