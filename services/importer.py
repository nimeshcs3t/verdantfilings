"""Import buys and sells from a broker's CSV export: guess columns, parse, check each row, then save."""
from __future__ import annotations

import io
import re
from datetime import date, datetime

import pandas as pd

from . import portfolio

FIELDS = {
    "date": ["trade date", "date", "transaction date", "settlement date", "execution date", "time", "datetime"],
    "symbol": ["symbol", "ticker", "code", "stock code", "security", "instrument", "isin", "asset"],
    "action": ["action", "type", "side", "buy/sell", "transaction type", "trade type", "direction", "activity"],
    "shares": ["quantity", "shares", "qty", "units", "volume", "amount of shares", "no. of shares", "number of shares"],
    "price": ["price", "trade price", "unit price", "execution price", "price per share", "avg price", "fill price"],
    "fees": ["fees", "fee", "commission", "brokerage", "charges", "costs"],
    "market": ["market", "exchange", "country", "listing", "venue"],
}
MARKETS = {"US": ["US", "USA", "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "NMS", "NYQ"],
           "KR": ["KR", "KRX", "KOSPI", "KOSDAQ", "KSE", "KOREA"],
           "AU": ["AU", "ASX", "AUS", "AUSTRALIA"], "PL": ["PL", "WSE", "GPW", "NEWCONNECT", "POLAND"],
           "JP": ["JP", "TSE", "TYO", "JPX", "JAPAN"], "IL": ["IL", "TASE", "TLV", "ISRAEL"]}
BUY_WORDS = ("buy", "bought", "purchase", "b", "long", "acquire")
SELL_WORDS = ("sell", "sold", "sale", "s", "short", "dispose")


def read(file_bytes: bytes) -> pd.DataFrame:
    """Read a CSV with any common separator and encoding."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:5000]
    sep = max([",", ";", "\t", "|"], key=sample.count)
    frame = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, skip_blank_lines=True)
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def guess_columns(columns: list[str]) -> dict[str, str | None]:
    lowered = {c: c.lower().strip() for c in columns}
    out: dict[str, str | None] = {}
    for field, names in FIELDS.items():
        match = next((c for n in names for c, low in lowered.items() if low == n), None)
        match = match or next((c for n in names for c, low in lowered.items() if n in low), None)
        out[field] = match if match not in out.values() else None
    return out


def number(text) -> float | None:
    t = str(text or "").strip()
    if not t:
        return None
    negative = t.startswith("(") and t.endswith(")") or t.startswith("-")
    t = re.sub(r"[^\d.,]", "", t)
    if "," in t and "." in t:
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", t) else t.replace(",", ".")
    try:
        value = float(t)
    except ValueError:
        return None
    return -value if negative else value


def parse_date(text: str, day_first: bool) -> date | None:
    t = str(text or "").strip()
    if not t:
        return None
    t = re.split(r"[ T]\d{1,2}:\d{2}", t)[0].strip()
    formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
               "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%b-%y"]
    formats += (["%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y"] if day_first
                else ["%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y"])
    for fmt in formats:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def day_first_guess(values: list[str]) -> bool:
    """True if dates look like 31/12/2025 rather than 12/31/2025."""
    for v in values:
        m = re.match(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}", str(v))
        if m and int(m[1]) > 12:
            return True
        if m and int(m[2]) > 12:
            return False
    return True


def market_of(text: str, default: str) -> str:
    t = str(text or "").strip().upper()
    for code, names in MARKETS.items():
        if t in names:
            return code
    return default


def plan(frame: pd.DataFrame, mapping: dict, default_market: str, day_first: bool, user_id: int,
         resolve) -> list[dict]:
    """Check every row. `resolve(market, symbol)` returns the company dict or None.
    Rows come back with status: ready, warning, duplicate or problem."""
    existing = portfolio.list_transactions(user_id)
    seen = {(t["market"], t["ticker"], t["tx_date"], t["kind"], round(t["shares"], 6), round(t["price"], 6)) for t in existing}
    rows = []
    for i, rec in frame.iterrows():
        get = lambda f: rec.get(mapping[f], "") if mapping.get(f) else ""
        row = {"line": i + 2, "status": "ready", "note": "", "kind": None}
        row["date"] = parse_date(get("date"), day_first)
        symbol = str(get("symbol")).strip()
        shares = number(get("shares"))
        price = number(get("price"))
        fees = abs(number(get("fees")) or 0.0)
        action = str(get("action")).strip().lower()
        if action.startswith(SELL_WORDS) or action in SELL_WORDS:
            kind = "sell"
        elif action.startswith(BUY_WORDS) or action in BUY_WORDS:
            kind = "buy"
        elif shares is not None:
            kind = "sell" if shares < 0 else "buy"
        else:
            kind = None
        if action and kind is None or any(w in action for w in ("dividend", "deposit", "withdraw", "interest", "fee", "split")):
            row.update(status="skip", note=f"Not a buy or sell ({action})")
            rows.append(row)
            continue
        market = market_of(get("market"), default_market)
        row.update(symbol=symbol, market=market, kind=kind, shares=abs(shares) if shares else None,
                   price=abs(price) if price is not None else None, fees=fees)
        missing = [n for n, v in (("date", row["date"]), ("symbol", symbol), ("quantity", row["shares"]),
                                  ("price", row["price"]), ("buy/sell", kind)) if not v]
        if missing:
            row.update(status="problem", note="Missing or unreadable " + ", ".join(missing))
            rows.append(row)
            continue
        if row["date"] > date.today():
            row.update(status="problem", note="Date is in the future")
            rows.append(row)
            continue
        comp = resolve(market, symbol)
        if not comp:
            row.update(status="problem", note=f"Couldn't find {symbol} in {market}")
            rows.append(row)
            continue
        row.update(ticker=comp["ticker"], name=comp.get("name_en") or comp["ticker"])
        use, note = portfolio.check_price(market, comp["ticker"], row["date"], row["price"])
        if note and use != row["price"]:
            row.update(price=use, note=note)
        elif note:
            row.update(status="warning", note=note)
        key = (market, comp["ticker"], row["date"], kind, round(row["shares"], 6), round(row["price"], 6))
        if key in seen:
            row.update(status="duplicate", note="Already in your transactions")
        rows.append(row)
    _check_sells(rows, existing)
    return rows


def _check_sells(rows: list[dict], existing: list[dict]) -> None:
    """Mark sells that would exceed the shares held at that point (existing trades plus earlier imported rows)."""
    from .portfolio_calc import positions
    good = [r for r in rows if r["status"] in ("ready", "warning")]
    trades = [dict(t) for t in existing]
    for r in sorted(good, key=lambda r: (r["date"], 0 if r["kind"] == "buy" else 1)):
        if r["kind"] == "sell":
            held = positions(trades, until=r["date"]).get((r["market"], r["ticker"]), {}).get("shares", 0)
            if r["shares"] > held + 1e-9:
                r.update(status="problem", note=f"Sells {r['shares']:,.4g} but only {held:,.4g} held then")
                continue
        trades.append({"id": 10 ** 9 + r["line"], "market": r["market"], "ticker": r["ticker"], "kind": r["kind"],
                       "tx_date": r["date"], "shares": r["shares"], "price": r["price"], "fees": r["fees"]})


def save(rows: list[dict], user_id: int, include_warnings: bool) -> tuple[int, list[str]]:
    saved, errors = 0, []
    wanted = [r for r in rows if r["status"] == "ready" or (include_warnings and r["status"] == "warning")]
    for r in sorted(wanted, key=lambda r: (r["date"], 0 if r["kind"] == "buy" else 1)):
        err = portfolio.add_transaction(user_id, r["market"], r["ticker"], r["kind"], r["date"], r["shares"], r["price"], r["fees"])
        if err:
            errors.append(f"Line {r['line']}: {err}")
        else:
            saved += 1
    return saved, errors
