"""Technical indicators for the company chart: daily open/high/low/close/volume from Yahoo Finance (fetched when the
chart is opened; the app's stored prices are closes only), EMA 21 / EMA 50 and RSI 14 (Wilder)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import requests

from .prices import HEADERS, MINOR, SUFFIX, symbol_for_isin


def _yahoo_ohlcv(symbol: str, rng: str) -> list[dict]:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"range": rng, "interval": "1d"}, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return []
    result = ((r.json().get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []
    meta = result.get("meta") or {}
    scale = 100.0 if meta.get("currency") in MINOR or symbol.upper().endswith(".TA") else 1.0
    offset = int(meta.get("gmtoffset") or 0)
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for i, ts in enumerate(result.get("timestamp") or []):
        close = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
        if close is None:
            continue
        pick = lambda k: (q.get(k) or [None] * (i + 1))[i]
        out.append({"date": datetime.fromtimestamp(ts + offset, tz=timezone.utc).date(),
                    "open": (pick("open") or close) / scale, "high": (pick("high") or close) / scale,
                    "low": (pick("low") or close) / scale, "close": close / scale, "volume": pick("volume") or 0})
    return out


def ohlcv(market: str, ticker: str, rng: str = "2y") -> list[dict]:
    """Daily bars, oldest first. Empty when Yahoo doesn't have the share."""
    if market == "FR":
        symbols = [symbol_for_isin(ticker) if len(ticker) == 12 else f"{ticker}.PA"]
    elif market == "X":
        symbols = [ticker]
    else:
        symbols = [f"{ticker}{s}" for s in SUFFIX.get(market, [""])]
    for sym in [s for s in symbols if s]:
        try:
            bars = _yahoo_ohlcv(sym, rng)
        except Exception:
            continue
        if len(bars) > 20:
            return bars
    return []


def ema(values: list[float], n: int) -> list[float | None]:
    """Exponential moving average; None until there are n values (seeded with their simple average)."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], n: int = 14) -> list[float | None]:
    """Relative Strength Index with Wilder's smoothing; None for the first n days."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n

    def value(g, l):
        return 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    out[n] = value(avg_g, avg_l)
    for i in range(n + 1, len(values)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = value(avg_g, avg_l)
    return out


def with_indicators(bars: list[dict]) -> list[dict]:
    closes = [b["close"] for b in bars]
    e21, e50, r14 = ema(closes, 21), ema(closes, 50), rsi(closes, 14)
    return [{**b, "ema21": a, "ema50": c, "rsi": r} for b, a, c, r in zip(bars, e21, e50, r14)]


def reading(rows: list[dict]) -> dict:
    """Plain-language state for the latest day: price vs the EMAs, RSI zone, volume vs its 50-day average."""
    if not rows:
        return {}
    last = rows[-1]
    vols = [r["volume"] for r in rows[-51:-1] if r["volume"]]
    avg_vol = sum(vols) / len(vols) if vols else None
    return {"close": last["close"], "ema21": last["ema21"], "ema50": last["ema50"], "rsi": last["rsi"],
            "volume": last["volume"], "vol_ratio": (last["volume"] / avg_vol) if avg_vol else None, "date": last["date"]}
