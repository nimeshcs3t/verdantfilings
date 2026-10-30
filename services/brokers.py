"""Broker exports other than IBKR, turned into the same shape ibkr.parse() returns so one import screen handles all:
{'period': date | None, 'broker': str, 'short': str, 'positions': [...], 'trades': [...]}

- Fidelity positions (Portfolio > Positions > Download): one row per holding per account; rows for the same symbol in
  several accounts are added together. Cash (SPAXX**), options and pending rows are left out.
- Fidelity activity (Activity & Orders > History > Download): "YOU BOUGHT" / "YOU SOLD" / reinvestment rows.
- Robinhood account activity report: Buy / Sell rows (stocks). Dividends, transfers and option trades are left out.
All US dollars."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime


def _text(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("latin-1", "ignore")


def _num(text) -> float | None:
    t = str(text or "").strip()
    if not t or t in ("--", "-", "n/a", "N/A"):
        return None
    negative = (t.startswith("(") and t.endswith(")")) or t.startswith("-") or t.startswith("$-") or t.startswith("-$")
    t = re.sub(r"[^\d.]", "", t)
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if negative else v


def _date(text) -> date | None:
    t = str(text or "").strip().split(" ")[0]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def _records(text: str, first_col: str) -> list[dict]:
    """CSV rows as dicts, starting at the header line whose first cell is `first_col` (skips preamble and footers)."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().strip('"').lower().startswith(first_col.lower())), None)
    if start is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    out = []
    for rec in reader:
        rec = {(k or "").strip().lower(): (v or "").strip() if isinstance(v, str) else v for k, v in rec.items()}
        out.append(rec)
    return out


def _header(text: str) -> str:
    for line in text.splitlines()[:12]:
        if line.strip():
            return line.strip().lower()
    return ""


def identify(file_bytes: bytes) -> str | None:
    from . import ibkr
    if ibkr.detect(file_bytes):
        return "ibkr"
    text = _text(file_bytes[:6000])
    head = _header(text)
    if "trans code" in head and "instrument" in head:
        return "robinhood"
    if head.startswith("symbol") and "average cost basis" in head and "last price" in head:
        return "fidelity-positions"
    if re.search(r"^\s*\"?run date\"?,.*action.*symbol", text, re.I | re.M):
        return "fidelity-activity"
    if head.startswith("account number") and "symbol" in head and "average cost basis" in head:
        return "fidelity-positions"
    return None


def _plain_stock(symbol: str) -> bool:
    s = symbol.strip()
    if not s or s.endswith("**") or s.startswith("-") or " " in s:
        return False                        # cash sweep, options (' -ONDS280121C15'), 'Pending Activity'
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-/]{0,9}", s.upper()))


def fidelity_positions(file_bytes: bytes) -> dict:
    text = _text(file_bytes)
    first = "account number" if _header(text).startswith("account number") else "symbol"
    merged: dict[str, dict] = {}
    for rec in _records(text, first):
        sym = (rec.get("symbol") or "").strip()
        if not _plain_stock(sym):
            continue
        qty = _num(rec.get("quantity"))
        if not qty:
            continue
        cost_total = _num(rec.get("cost basis total"))
        avg = _num(rec.get("average cost basis"))
        cost_total = cost_total if cost_total is not None else (avg * qty if avg is not None else None)
        m = merged.setdefault(sym.upper(), {"shares": 0.0, "cost": 0.0, "cost_known": True, "close": None})
        m["shares"] += qty
        if cost_total is None:
            m["cost_known"] = False
        else:
            m["cost"] += cost_total
        m["close"] = _num(rec.get("last price")) or m["close"]
    positions = [{"currency": "USD", "symbol": s, "shares": round(m["shares"], 6),
                  "cost_price": (m["cost"] / m["shares"]) if m["cost_known"] and m["shares"] else m["close"],
                  "close_price": m["close"]} for s, m in merged.items()]
    return {"period": None, "broker": "Fidelity", "short": "Fidelity", "positions": positions, "trades": []}


def fidelity_activity(file_bytes: bytes) -> dict:
    trades = []
    for rec in _records(_text(file_bytes), "run date"):
        action = (rec.get("action") or "").upper()
        sym = (rec.get("symbol") or "").strip()
        if not _plain_stock(sym):
            continue
        if "YOU BOUGHT" in action or action.startswith("REINVESTMENT"):
            kind = "buy"
        elif "YOU SOLD" in action:
            kind = "sell"
        else:
            continue
        qty = _num(rec.get("quantity"))
        price = _num(rec.get("price ($)") or rec.get("price"))
        day = _date(rec.get("run date"))
        if not qty or price is None or not day:
            continue
        fees = abs(_num(rec.get("commission ($)")) or 0) + abs(_num(rec.get("fees ($)")) or 0)
        trades.append({"currency": "USD", "symbol": sym.upper(), "date": day, "kind": kind, "shares": abs(qty),
                       "price": abs(price), "fees": fees})
    return {"period": None, "broker": "Fidelity", "short": "Fidelity", "positions": [], "trades": trades}


def robinhood(file_bytes: bytes) -> dict:
    trades = []
    for rec in _records(_text(file_bytes), "activity date"):
        code = (rec.get("trans code") or "").strip().lower()
        if code not in ("buy", "sell"):
            continue                        # CDIV, ACH, BTO/STC (options), SPL, etc.
        sym = (rec.get("instrument") or "").strip()
        qty = _num(rec.get("quantity"))
        price = _num(rec.get("price"))
        day = _date(rec.get("activity date"))
        if not _plain_stock(sym) or not qty or price is None or not day:
            continue
        trades.append({"currency": "USD", "symbol": sym.upper(), "date": day, "kind": code, "shares": abs(qty),
                       "price": abs(price), "fees": 0.0})
    return {"period": None, "broker": "Robinhood", "short": "Robinhood", "positions": [], "trades": trades}


def parse(kind: str, file_bytes: bytes) -> dict:
    from . import ibkr
    if kind == "ibkr":
        out = ibkr.parse(file_bytes)
        out["short"] = "IBKR"
        return out
    return {"fidelity-positions": fidelity_positions, "fidelity-activity": fidelity_activity,
            "robinhood": robinhood}[kind](file_bytes)
