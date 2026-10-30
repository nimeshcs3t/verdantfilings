"""Interactive Brokers statements (Activity Statement CSV): several tables stacked in one file, each row starting
with the table name and Header/Data/Total. Reads the Open Positions and Trades tables.

Positions are compared with the member's holdings and only the differences are proposed (new holding, more shares,
fewer shares), so importing the same statement twice changes nothing. Trades go through the normal import checks."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

CURRENCY_MARKET = {"USD": "US", "AUD": "AU", "KRW": "KR", "ILS": "IL", "JPY": "JP", "HKD": "HK", "PLN": "PL",
                   "NOK": "NO", "SEK": "SE", "DKK": "DK", "TWD": "TW", "GBP": "UK"}
# Yahoo Finance suffixes to try for currencies the app has no filings source for (or several exchanges share)
YAHOO_SUFFIXES = {"EUR": [".PA", ".DE", ".AS", ".MI", ".BR", ".MC", ".LS", ".HE", ".IR", ".VI"], "SGD": [".SI"],
                  "CAD": [".TO", ".V", ".NE"], "CHF": [".SW"], "INR": [".NS", ".BO"], "GBP": [".L"], "ZAR": [".JO"],
                  "MXN": [".MX"], "BRL": [".SA"], "NZD": [".NZ"], "CNH": [".HK"], "HKD": [".HK"], "SEK": [".ST"],
                  "NOK": [".OL"], "DKK": [".CO"], "PLN": [".WA"], "JPY": [".T"], "AUD": [".AX"], "ILS": [".TA"],
                  "KRW": [".KS", ".KQ"], "TWD": [".TW", ".TWO"], "USD": [""]}
EUR_MARKETS = ["FR", "FI"]          # euro markets with a filings source in the app


def _rows(file_bytes: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return [r for r in csv.reader(io.StringIO(text)) if r]


def detect(file_bytes: bytes) -> bool:
    head = file_bytes[:3000].decode("utf-8-sig", "ignore")
    return bool(re.search(r"^(Statement|Open Positions|Trades|Account Information),Header,", head, re.M))


def _num(text) -> float | None:
    t = str(text or "").replace(",", "").strip()
    try:
        return float(t) if t not in ("", "-", "--") else None
    except ValueError:
        return None


def parse(file_bytes: bytes) -> dict:
    """{'period': date or None, 'broker': str, 'positions': [...], 'trades': [...]}"""
    headers: dict[str, list[str]] = {}
    out = {"period": None, "broker": "Interactive Brokers", "positions": [], "trades": []}
    for row in _rows(file_bytes):
        section, kind, rest = row[0].strip(), (row[1].strip() if len(row) > 1 else ""), row[2:]
        if kind == "Header":
            headers[section] = [c.strip() for c in rest]
            continue
        if kind != "Data" or section not in headers:
            continue
        rec = dict(zip(headers[section], [c.strip() for c in rest]))
        if section == "Statement":
            if rec.get("Field Name") == "Period":
                value = rec.get("Field Value", "")
                last = value.split(" - ")[-1].strip()
                for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
                    try:
                        out["period"] = datetime.strptime(last, fmt).date()
                        break
                    except ValueError:
                        continue
            elif rec.get("Field Name") == "BrokerName":
                out["broker"] = rec.get("Field Value") or out["broker"]
        elif section == "Open Positions":
            if rec.get("DataDiscriminator", "Summary") != "Summary" or rec.get("Asset Category", "Stocks") != "Stocks":
                continue
            qty = _num(rec.get("Quantity"))
            if not qty:
                continue
            out["positions"].append({"currency": rec.get("Currency", "").upper(), "symbol": rec.get("Symbol", ""),
                                     "shares": qty * (_num(rec.get("Mult")) or 1), "cost_price": _num(rec.get("Cost Price")),
                                     "close_price": _num(rec.get("Close Price"))})
        elif section == "Trades":
            if rec.get("DataDiscriminator", "Order") not in ("Order", "Trade") or rec.get("Asset Category", "Stocks") != "Stocks":
                continue
            qty, price = _num(rec.get("Quantity")), _num(rec.get("T. Price") or rec.get("Price"))
            when = str(rec.get("Date/Time") or rec.get("TradeDate") or "").split(",")[0].strip()
            try:
                day = date.fromisoformat(when) if "-" in when else datetime.strptime(when, "%Y%m%d").date()
            except ValueError:
                day = None
            if not qty or price is None:
                continue
            out["trades"].append({"currency": rec.get("Currency", "").upper(), "symbol": rec.get("Symbol", ""), "date": day,
                                  "kind": "buy" if qty > 0 else "sell", "shares": abs(qty), "price": price,
                                  "fees": abs(_num(rec.get("Comm/Fee")) or 0.0)})
    return out


def resolve(currency: str, symbol: str, resolver, configured: set[str]) -> tuple[str, str, str] | None:
    """(market, ticker, name) for an IBKR currency + symbol, or None. `resolver(market, symbol)` is the app's company
    lookup (returns a company dict); market 'X' means any-market holdings by Yahoo symbol."""
    from .portfolio import OTHER
    sym = symbol.strip().upper()
    base = re.sub(r"\.(KQ|KS)$", "", sym)
    markets = [CURRENCY_MARKET[currency]] if currency in CURRENCY_MARKET else (EUR_MARKETS if currency == "EUR" else [])
    for market in markets:
        if market not in configured:
            continue
        try:
            comp = resolver(market, base)
        except Exception:
            comp = None
        if comp:
            return market, comp["ticker"], comp.get("name_en") or comp["ticker"]
    for suffix in YAHOO_SUFFIXES.get(currency, []):
        candidate = sym if "." in sym and currency in ("KRW",) else f"{base}{suffix}"
        try:
            comp = resolver(OTHER, candidate)
        except Exception:
            comp = None
        if comp:
            return OTHER, comp["ticker"], comp.get("name_en") or comp["ticker"]
    return None


def plan_positions(parsed: dict, holdings: list[dict], resolver, configured: set[str]) -> list[dict]:
    who = parsed.get("short") or "IBKR"
    """One row per broker position: what the app holds, the difference, and the transaction that would fix it."""
    held = {(h["market"], h["ticker"]): h["shares"] for h in holdings}
    rows = []
    for p in parsed["positions"]:
        row = {"symbol": p["symbol"], "currency": p["currency"], "broker": p["shares"], "price": p["cost_price"],
               "close": p["close_price"], "apply": False}
        found = resolve(p["currency"], p["symbol"], resolver, configured)
        if not found:
            row.update(status="not found", note=f"Couldn't match {p['symbol']} ({p['currency']}) to a listed share")
            rows.append(row)
            continue
        market, ticker, name = found
        app = held.get((market, ticker), 0.0)
        diff = p["shares"] - app
        row.update(market=market, ticker=ticker, name=name, app=app, diff=diff)
        if abs(diff) < 1e-6:
            row.update(status="matches", note="Already the same")
        elif app == 0:
            row.update(status="new", kind="buy", shares=p["shares"], apply=True,
                       note=f"New holding at {who}'s average cost")
        elif diff > 0:
            row.update(status="more", kind="buy", shares=diff, apply=True,
                       note=f"{who} shows {diff:,.4g} more; added at {who}'s average cost")
        else:
            row.update(status="fewer", kind="sell", shares=-diff, price=p["close_price"] or p["cost_price"],
                       note=f"{who} shows {-diff:,.4g} fewer; tick to record a sale at the latest price in the file "
                            "(leave unticked if you hold the rest at another broker)")
        if not row.get("price"):
            row.update(status="not found", apply=False, note="No price in the statement")
        rows.append(row)
    return rows


def apply_positions(rows: list[dict], user_id: int, on: date) -> tuple[int, list[str]]:
    from . import portfolio
    saved, errors = 0, []
    for r in sorted([r for r in rows if r.get("apply") and r.get("kind")], key=lambda r: r["kind"] != "buy"):
        err = portfolio.add_transaction(user_id, r["market"], r["ticker"], r["kind"], on, r["shares"], r["price"], 0.0)
        if err:
            errors.append(f"{r['symbol']}: {err}")
        else:
            saved += 1
    return saved, errors
