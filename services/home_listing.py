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


@lru_cache(maxsize=512)
def find(symbol: str, name_hint: str = "") -> tuple[str, str, str] | None:
    """(market, ticker, name) of the home listing of a US OTC symbol, on a market the app has, or None."""
    from sources import configured_sources, get_source
    from .prices import symbol_details, yahoo_search
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
