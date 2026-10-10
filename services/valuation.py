"""Quick view: price statistics for any company, plus market cap, P/E, P/S, margin and growth where shares and
financial figures are stored (Korea, USA, Taiwan, Europe/UK)."""
from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy import select

from core.db import company_meta, get_engine

from . import financials, prices


def _usd_per(cur: str) -> float | None:
    if not cur or cur.upper() == "USD":
        return 1.0
    hist = prices.fx_history(cur.upper())
    return hist[-1][1] if hist else None


def shares(market: str, ticker: str) -> float | None:
    with get_engine().connect() as conn:
        return conn.execute(select(company_meta.c.shares).where(company_meta.c.market == market,
                                                                company_meta.c.ticker == ticker)).scalar()


def quick_view(market: str, ticker: str, hist: list, trade_currency: str) -> dict:
    out: dict = {"currency": trade_currency}
    if hist:
        price = hist[-1][1]
        year = [(d, v) for d, v in hist if d >= hist[-1][0] - timedelta(days=365)]
        closes = [v for _, v in year]
        out.update(price=price, high52=max(closes), low52=min(closes),
                   ret1y=(price / closes[0] - 1) if closes and closes[0] else None)
        rets = [b / a - 1 for a, b in zip(closes, closes[1:]) if a]
        if len(rets) > 20:
            mean = sum(rets) / len(rets)
            out["volatility"] = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
        peak, worst = 0.0, 0.0
        for v in closes:
            peak = max(peak, v)
            worst = min(worst, v / peak - 1) if peak else worst
        out["drawdown"] = worst
    fin = financials.get(market, ticker)
    annual, quarter = fin["annual"], fin["quarter"]
    latest, approx = (annual[-1] if annual else None), False
    if not latest and quarter and "YTD" in quarter[-1]["period"]:     # Taiwan: year to date, scaled to a full year
        q = quarter[-1]
        n = int(q["period"].split("Q")[1].split()[0])
        latest = {**q, **{k: (q[k] * 4 / n if q.get(k) is not None else None) for k in ("revenue", "op_income", "net_income")}}
        approx = True
    if latest:
        out.update(revenue=latest.get("revenue"), net_income=latest.get("net_income"), fin_currency=latest.get("currency"),
                   fin_period=latest["period"] + (" (annualised)" if approx else ""))
        if latest.get("revenue") and latest.get("op_income") is not None:
            out["op_margin"] = latest["op_income"] / latest["revenue"]
        if latest.get("revenue") and latest.get("net_income") is not None:
            out["net_margin"] = latest["net_income"] / latest["revenue"]
        if len(annual) >= 2 and annual[-2].get("revenue") and annual[-1].get("revenue"):
            out["growth"] = annual[-1]["revenue"] / annual[-2]["revenue"] - 1
    n_shares = shares(market, ticker)
    if n_shares and out.get("price"):
        mcap = n_shares * out["price"]
        out["market_cap"] = mcap
        if latest:
            a, b = _usd_per(latest.get("currency") or trade_currency), _usd_per(trade_currency)
            rate = (a / b) if a and b else None              # financial currency -> trading currency
            if rate:
                if latest.get("net_income") and latest["net_income"] > 0:
                    out["pe"] = mcap / (latest["net_income"] * rate)
                if latest.get("revenue"):
                    out["ps"] = mcap / (latest["revenue"] * rate)
    return out


ROWS = [("Price", "price", "money"), ("Market cap", "market_cap", "big"), ("P/E", "pe", "x"), ("Price / sales", "ps", "x"),
        ("Revenue", "revenue", "fin"), ("Revenue growth", "growth", "pct"), ("Operating margin", "op_margin", "pct"),
        ("Net margin", "net_margin", "pct"), ("1-year return", "ret1y", "pct"), ("52-week range", "range", "range"),
        ("Volatility (1y)", "volatility", "pct0"), ("Worst drop (1y)", "drawdown", "pct")]


def fmt(q: dict, key: str, kind: str) -> str:
    from .financials import money
    cur = q.get("currency") or ""
    if kind == "range":
        return f'{q["low52"]:,.2f}–{q["high52"]:,.2f}' if q.get("low52") is not None else "–"
    v = q.get(key)
    if v is None:
        return "–"
    if kind == "money":
        return f"{v:,.2f} {cur}"
    if kind == "big":
        return money(v, cur)
    if kind == "fin":
        return money(v, q.get("fin_currency") or "")
    if kind == "x":
        return f"{v:,.1f}×"
    if kind == "pct0":
        return f"{v * 100:.0f}%"
    return f"{v * 100:+.1f}%"
