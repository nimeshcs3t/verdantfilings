"""Daily share prices from free sources (Yahoo Finance chart data, then Stooq). Used for the price move on
each filing, sparklines and company charts. Prices are indicative and may be delayed."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0.0.0 Safari/537.36"}
SUFFIX = {"KR": [".KS", ".KQ"], "US": [""], "AU": [".AX"], "JP": [".T"], "PL": [".WA"], "IL": [".TA"], "HK": [".HK"],
          "NO": [".OL"], "TW": [".TW", ".TWO"], "UK": [".L"], "SE": [".ST"], "DK": [".CO"], "FI": [".HE"]}
MINOR = {"GBp", "GBX", "ILA", "ZAc"}       # prices quoted in pence / agorot / cents
STOOQ = {"US": ".us", "JP": ".jp", "PL": ""}


def _yahoo(symbol: str, years: int = 1) -> list[tuple[date, float]]:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"range": f"{years}y", "interval": "1d"}, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return []
    result = ((r.json().get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []
    scale = 100.0 if (result.get("meta") or {}).get("currency") in MINOR else 1.0
    offset = int((result.get("meta") or {}).get("gmtoffset") or 0)
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    out = []
    for ts, close in zip(result.get("timestamp") or [], closes):
        if close is not None:
            out.append((datetime.fromtimestamp(ts + offset, tz=timezone.utc).date(), float(close) / scale))
    return out


def _stooq(symbol: str) -> list[tuple[date, float]]:
    r = requests.get("https://stooq.com/q/d/l/", params={"s": symbol, "i": "d"}, headers=HEADERS, timeout=15)
    if r.status_code != 200 or not r.text.startswith("Date"):
        return []
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            out.append((date.fromisoformat(row["Date"]), float(row["Close"])))
        except (KeyError, ValueError):
            continue
    return out


def yahoo_search(query: str, exchanges: tuple[str, ...] = (), limit: int = 8) -> list[dict]:
    """Yahoo Finance symbol search, optionally limited to exchange codes (e.g. LSE, STO, CPH, HEL, PAR)."""
    try:
        r = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                         params={"q": query, "quotesCount": 15, "newsCount": 0}, headers=HEADERS, timeout=20)
        quotes = r.json().get("quotes", []) if r.status_code == 200 else []
    except Exception:
        return []
    out = [q for q in quotes if q.get("symbol") and (not exchanges or q.get("exchange") in exchanges)
           and q.get("quoteType") in (None, "EQUITY")]
    return out[:limit]


_ISIN_SYMBOLS: dict[str, str | None] = {}


def symbol_for_isin(isin: str, exchanges: tuple[str, ...] = ("PAR",)) -> str | None:
    if isin not in _ISIN_SYMBOLS:
        found = yahoo_search(isin, exchanges, 1)
        _ISIN_SYMBOLS[isin] = found[0]["symbol"] if found else None
    return _ISIN_SYMBOLS[isin]


def history(market: str, ticker: str, years: int = 1) -> list[tuple[date, float]]:
    """Daily closes, oldest first (about `years` years). Empty if no free source has the stock."""
    if market == "FR":
        symbol = symbol_for_isin(ticker) if len(ticker) == 12 else f"{ticker}.PA"
        try:
            return _yahoo(symbol, years) if symbol else []
        except Exception:
            return []
    for suffix in SUFFIX.get(market, []):
        try:
            data = _yahoo(f"{ticker}{suffix}", years)
            if len(data) > 5:
                return data
        except Exception:
            continue
    if market in STOOQ:
        try:
            return _stooq(f"{ticker.lower()}{STOOQ[market]}")[-(260 * years):]
        except Exception:
            return []
    return []


def symbol_details(symbol: str) -> dict | None:
    """Name, trading currency and exchange for any Yahoo Finance symbol, or None if it doesn't exist."""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                         params={"range": "5d", "interval": "1d"}, headers=HEADERS, timeout=20)
        result = ((r.json().get("chart") or {}).get("result") or [None])[0] if r.status_code == 200 else None
    except Exception:
        result = None
    if not result:
        return None
    meta = result.get("meta") or {}
    if not meta.get("currency"):
        return None
    return {"name": meta.get("longName") or meta.get("shortName") or symbol, "currency": meta["currency"],
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or ""}


def symbol_history(symbol: str, years: int = 1) -> list[tuple[date, float]]:
    """Any Yahoo symbol (benchmarks such as SPY or QQQ)."""
    try:
        data = _yahoo(symbol, years)
        if len(data) > 5:
            return data
    except Exception:
        pass
    if "." in symbol:
        return []
    try:
        return _stooq(f"{symbol.lower()}.us")[-(260 * years):]
    except Exception:
        return []


def fx_history(currency: str, years: int = 1) -> list[tuple[date, float]]:
    """US dollars per one unit of `currency`, daily. [] for USD (rate is 1)."""
    currency = currency.upper()
    if currency == "USD":
        return []
    for symbol, invert in ((f"{currency}USD=X", False), (f"{currency}=X", True)):
        try:
            data = _yahoo(symbol, years)
        except Exception:
            data = []
        data = [(d, (1 / v if invert else v)) for d, v in data if v]
        if len(data) > 5:
            return data
    try:
        return _stooq(f"{currency.lower()}usd")[-(260 * years):]
    except Exception:
        return []


def move_on(hist: list[tuple[date, float]], day: date) -> float | None:
    """Price change on the filing day (or the next trading day), versus the previous close."""
    for i, (d, close) in enumerate(hist):
        if d >= day and i > 0:
            prev = hist[i - 1][1]
            return (close / prev - 1) if prev else None
    return None


def last_move(hist: list[tuple[date, float]]) -> float | None:
    return (hist[-1][1] / hist[-2][1] - 1) if len(hist) >= 2 and hist[-2][1] else None


def move_html(move: float | None) -> str:
    if move is None:
        return ""
    cls = "up" if move > 0.0005 else "down" if move < -0.0005 else "flat"
    arrow = "▲" if cls == "up" else "▼" if cls == "down" else "■"
    return f'<span class="mv {cls}" title="Share price change that day">{arrow} {abs(move) * 100:.1f}%</span>'


def sparkline_svg(hist: list[tuple[date, float]], days: int = 30, width: int = 96, height: int = 26) -> str:
    pts = [c for _, c in hist[-days:]]
    if len(pts) < 3:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1
    xy = " ".join(f"{i * (width - 2) / (len(pts) - 1) + 1:.1f},{height - 2 - (p - lo) / span * (height - 4):.1f}"
                  for i, p in enumerate(pts))
    cls = "up" if pts[-1] >= pts[0] else "down"
    return (f'<svg class="spark {cls}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'aria-label="30-day price trend"><polyline fill="none" stroke-width="1.6" points="{xy}"/></svg>')


def chart_svg(hist: list[tuple[date, float]], markers: list[date], days: int = 120) -> str:
    """Responsive price chart with a dot on each filing date."""
    data = hist[-days:]
    if len(data) < 5:
        return ""
    w, h, pad = 720, 180, 8
    closes = [c for _, c in data]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1
    x = lambda i: pad + i * (w - 2 * pad) / (len(data) - 1)
    y = lambda c: h - pad - (c - lo) / span * (h - 2 * pad)
    line = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, (_, c) in enumerate(data))
    area = f"{x(0):.1f},{h - pad} {line} {x(len(data) - 1):.1f},{h - pad}"
    dots = []
    marked = set(markers)
    for i, (d, c) in enumerate(data):
        if d in marked or (i + 1 < len(data) and any(d < m < data[i + 1][0] for m in marked)):
            dots.append(f'<circle cx="{x(i):.1f}" cy="{y(c):.1f}" r="3.5"><title>Filing on {d:%d %b}</title></circle>')
    first, last = data[0], data[-1]
    change = last[1] / first[1] - 1 if first[1] else 0
    return (f'<div class="chart"><div class="chart-head"><span>{len(data)} trading days</span>'
            f'<span class="mv {"up" if change >= 0 else "down"}">{change * 100:+.1f}%</span>'
            f'<span class="chart-last">Last close {last[1]:,.2f} on {last[0]:%d %b}</span></div>'
            f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" aria-label="Share price chart">'
            f'<polygon class="area" points="{area}"/><polyline class="line" fill="none" points="{line}"/>'
            f'<g class="dots">{"".join(dots)}</g></svg>'
            f'<div class="chart-foot">Dots mark filing dates. Prices from free sources, may be delayed.</div></div>')
