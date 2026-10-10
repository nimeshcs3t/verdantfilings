"""Portfolio maths, kept free of database and web code so it can be tested on its own.

Values are converted to a base currency day by day. Returns are time-weighted (each day's change is measured
after that day's buys and sells), which is the standard way to compare with a benchmark."""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta

PERIODS = [("1D", None), ("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182), ("YTD", "ytd"), ("1Y", 365), ("2Y", 730),
           ("3Y", 1095), ("All", "all")]


class Series:
    """A step function over dates: the value on any day is the latest known value on or before it."""

    def __init__(self, points: list[tuple[date, float]], default: float | None = None):
        pts = sorted((d, v) for d, v in points if v is not None)
        self.dates = [d for d, _ in pts]
        self.values = [v for _, v in pts]
        self.default = default

    def at(self, day: date) -> float | None:
        i = bisect_right(self.dates, day)
        if i:
            return self.values[i - 1]
        return self.values[0] if self.values else self.default   # before history starts: earliest known


def positions(txs: list[dict], until: date | None = None, convert=None) -> dict[tuple[str, str], dict]:
    """Shares and average cost per holding (average-cost method; sells reduce cost proportionally).
    With `convert(amount, key, day)`, also tracks cost and realised gain in a base currency at trade-date rates."""
    conv = convert or (lambda amount, key, day: amount)
    out: dict[tuple[str, str], dict] = {}
    for t in sorted(txs, key=lambda t: (t["tx_date"], t.get("id") or 0)):
        if until and t["tx_date"] > until:
            continue
        key = (t["market"], t["ticker"])
        p = out.setdefault(key, {"shares": 0.0, "cost": 0.0, "realized": 0.0, "cost_base": 0.0, "realized_base": 0.0})
        fees = t.get("fees") or 0
        if t["kind"] == "buy":
            p["shares"] += t["shares"]
            p["cost"] += t["shares"] * t["price"] + fees
            p["cost_base"] += conv(t["shares"] * t["price"] + fees, key, t["tx_date"]) or 0
        else:
            if p["shares"] <= 0:
                continue
            sold = min(t["shares"], p["shares"])
            share = sold / p["shares"]
            avg = p["cost"] / p["shares"]
            p["realized"] += sold * t["price"] - fees - sold * avg
            p["realized_base"] += (conv(sold * t["price"] - fees, key, t["tx_date"]) or 0) - p["cost_base"] * share
            p["cost"] -= avg * sold
            p["cost_base"] -= p["cost_base"] * share
            p["shares"] -= sold
    for p in out.values():
        p["avg"] = p["cost"] / p["shares"] if p["shares"] > 1e-12 else 0.0
    return out


def daily_values(txs: list[dict], prices: dict[tuple[str, str], Series], fx: dict[str, Series],
                 currency_of: dict[tuple[str, str], str], base: str, trading_days: list[date],
                 base_fx: Series | None = None, cash_moves: list[dict] | None = None) -> list[dict]:
    """For each trading day from the first transaction: portfolio value and money added, in the base currency.
    `flow` is money at the actual trade prices; `flow_mkt` values the same trades at that day's closing prices.

    With cash_moves (deposits, withdrawals, interest, dividends, fees), the value includes cash in each currency,
    trades just move money between cash and shares, and only deposits and withdrawals count as money added."""
    cash_moves = sorted(cash_moves or [], key=lambda m: (m["move_date"], m.get("id") or 0))
    if not txs and not cash_moves:
        return []
    txs = sorted(txs, key=lambda t: (t["tx_date"], t.get("id") or 0))
    start = min([t["tx_date"] for t in txs] + [m["move_date"] for m in cash_moves])
    days = sorted({d for d in trading_days if d >= start} | {t["tx_date"] for t in txs} | {m["move_date"] for m in cash_moves})
    account = bool(cash_moves)
    cash: dict[str, float] = {}
    j = 0

    def to_base(amount: float, cur: str, day: date) -> float | None:
        usd = amount if cur == "USD" else (amount * fx[cur].at(day) if cur in fx and fx[cur].at(day) else None)
        if usd is None:
            return None
        if base == "USD":
            return usd
        rate = base_fx.at(day) if base_fx else None
        return usd / rate if rate else None

    held: dict[tuple[str, str], float] = {}
    out, i = [], 0
    for day in days:
        flow, flow_mkt = 0.0, 0.0
        while i < len(txs) and txs[i]["tx_date"] <= day:
            t = txs[i]
            key = (t["market"], t["ticker"])
            cur = currency_of.get(key, "USD")
            fees = t.get("fees") or 0
            close = prices[key].at(t["tx_date"]) if key in prices else None
            if t["kind"] == "buy":
                held[key] = held.get(key, 0.0) + t["shares"]
                if account:
                    cash[cur] = cash.get(cur, 0.0) - (t["shares"] * t["price"] + fees)
                else:
                    flow += to_base(t["shares"] * t["price"] + fees, cur, t["tx_date"]) or 0.0
                    flow_mkt += to_base(t["shares"] * (close if close is not None else t["price"]), cur, day) or 0.0
            else:
                sold = min(t["shares"], held.get(key, 0.0))
                held[key] = held.get(key, 0.0) - sold
                if account:
                    cash[cur] = cash.get(cur, 0.0) + (sold * t["price"] - fees)
                else:
                    flow -= to_base(sold * t["price"] - fees, cur, t["tx_date"]) or 0.0
                    flow_mkt -= to_base(sold * (close if close is not None else t["price"]), cur, day) or 0.0
            i += 1
        while j < len(cash_moves) and cash_moves[j]["move_date"] <= day:
            m = cash_moves[j]
            sign = -1.0 if m["kind"] in ("withdraw", "fee") else 1.0
            cash[m["currency"]] = cash.get(m["currency"], 0.0) + sign * m["amount"]
            if m["kind"] in ("deposit", "withdraw"):          # only money in or out of the account is a flow
                moved = to_base(sign * m["amount"], m["currency"], day) or 0.0
                flow += moved
                flow_mkt += moved
            j += 1
        total, ok = 0.0, True
        for key, shares in held.items():
            if shares <= 1e-12:
                continue
            price = prices[key].at(day) if key in prices else None
            v = to_base(shares * price, currency_of.get(key, "USD"), day) if price is not None else None
            if v is None:
                ok = False
                break
            total += v
        cash_total = 0.0
        for cur, amount in cash.items():
            if abs(amount) > 1e-9:
                v = to_base(amount, cur, day)
                if v is None:
                    ok = False
                    break
                cash_total += v
        if ok:
            out.append({"date": day, "value": total + cash_total, "flow": flow, "flow_mkt": flow_mkt, "cash": cash_total})
    return out


def cash_balances(txs: list[dict], cash_moves: list[dict]) -> dict[str, float]:
    """Cash left in each currency after deposits, withdrawals, income, fees and every buy and sell."""
    if not cash_moves:
        return {}
    bal: dict[str, float] = {}
    for m in cash_moves:
        sign = -1.0 if m["kind"] in ("withdraw", "fee") else 1.0
        bal[m["currency"]] = bal.get(m["currency"], 0.0) + sign * m["amount"]
    return bal


def twr_index(daily: list[dict]) -> list[tuple[date, float]]:
    """Cumulative time-weighted growth of 1 unit.

    Each day's return is measured on the holdings already owned: (value at close - trades valued at close) /
    previous close. New money joins at the closing price, so a mistyped trade price can't distort returns.
    On the very first day the return runs from the trade price to the close, unless that gap is implausible."""
    index, prev, level = [], 0.0, 1.0
    for row in daily:
        value = row["value"]
        flow_mkt = row.get("flow_mkt", row["flow"])
        if prev > 1e-9:
            factor = (value - flow_mkt) / prev
            if factor > 0:
                level *= factor
        elif row["flow"] > 1e-9:
            first = value / row["flow"]
            if 0.67 <= first <= 1.5:          # beyond this, the entered price is more likely a typo than a real move
                level *= first
        index.append((row["date"], level))
        prev = value
    return index


def _start_for(label, spec, end: date, first: date) -> date | None:
    if spec is None:
        return None
    if spec == "ytd":
        return date(end.year, 1, 1) - timedelta(days=1)
    if spec == "all":
        return first - timedelta(days=1)
    return end - timedelta(days=spec)


def period_returns(index: list[tuple[date, float]], bench: Series | None = None) -> list[dict]:
    """Portfolio and benchmark return for each standard period. Periods before the first purchase show as None."""
    if len(index) < 1:
        return []
    idx = Series(index)
    first, end = index[0][0], index[-1][0]
    out = []
    for label, spec in PERIODS:
        if spec is None:
            prev = index[-2][1] if len(index) >= 2 else 1.0
            port = index[-1][1] / prev - 1 if prev else None
            b_prev_day = index[-2][0] if len(index) >= 2 else None
            bm = (bench.at(end) / bench.at(b_prev_day) - 1) if bench and b_prev_day and bench.at(b_prev_day) else None
            out.append({"label": label, "portfolio": port, "benchmark": bm, "partial": False})
            continue
        start = _start_for(label, spec, end, first)
        partial = start < first - timedelta(days=1)
        base_level = idx.at(start) if start >= first else 1.0
        port = index[-1][1] / base_level - 1 if base_level else None
        b_start = max(start, first - timedelta(days=1)) if partial else start
        bm = (bench.at(end) / bench.at(b_start) - 1) if bench and bench.at(b_start) else None
        out.append({"label": label, "portfolio": port, "benchmark": bm, "partial": partial})
    return out


def allocation(rows: list[dict], key: str, top: int = 7) -> list[tuple[str, float]]:
    """Share of total value grouped by `key`, largest first; small groups folded into 'Other'."""
    totals: dict[str, float] = {}
    for r in rows:
        if r.get("value_base"):
            totals[r[key]] = totals.get(r[key], 0.0) + r["value_base"]
    whole = sum(totals.values()) or 1
    items = sorted(((k, v / whole) for k, v in totals.items()), key=lambda kv: -kv[1])
    if len(items) > top:
        items = items[:top - 1] + [("Other", sum(v for _, v in items[top - 1:]))]
    return items


def rebalance(rows: list[dict], targets: dict[tuple[str, str], float], new_cash: float = 0.0) -> list[dict]:
    """Trades (in shares) to move from current weights to target weights, spending `new_cash` (base currency)."""
    total = sum(r["value_base"] or 0 for r in rows) + new_cash
    out = []
    for r in rows:
        key = (r["market"], r["ticker"])
        current = (r["value_base"] or 0) / total if total else 0
        target = targets.get(key)
        if target is None:
            out.append({**r, "current": current, "target": None, "trade_value": 0.0, "trade_shares": 0.0})
            continue
        trade_value = total * target / 100 - (r["value_base"] or 0)
        per_share = r["price_base"] or 0
        out.append({**r, "current": current, "target": target / 100, "trade_value": trade_value,
                    "trade_shares": (trade_value / per_share) if per_share else 0.0})
    return out


# ---- money-weighted returns ---------------------------------------------------------------------------
def shadow_benchmark(daily: list[dict], bench: Series) -> list[dict]:
    """The same money, on the same days, put into the benchmark instead: buys add units, sells remove them."""
    units, out = 0.0, []
    for row in daily:
        price = bench.at(row["date"])
        if not price:
            continue
        units = max(units + row["flow"] / price, 0.0)
        out.append({"date": row["date"], "value": units * price, "flow": row["flow"]})
    return out


def money_series(daily: list[dict], start: date) -> list[tuple[date, float]]:
    """Return on your money from `start` to each later day:
    gain in the period / (value at the start + money added in the period). For the whole history this equals
    total gain / total invested, matching the Total gain figure."""
    before = [r for r in daily if r["date"] < start]
    v0 = before[-1]["value"] if before else 0.0
    net, added, out = 0.0, 0.0, []
    for r in daily:
        if r["date"] < start:
            continue
        net += r["flow"]
        added += max(r["flow"], 0.0)
        base = v0 + added
        if base > 1e-9:
            out.append((r["date"], (r["value"] - v0 - net) / base))
    return out


def money_weighted_returns(daily: list[dict], bench_daily: list[dict] | None = None) -> list[dict]:
    """Money-weighted return for each standard period, and the same for the benchmark bought with the same money."""
    if not daily:
        return []
    first, end = daily[0]["date"], daily[-1]["date"]
    out = []
    for label, spec in PERIODS:
        if spec is None:
            prior = [r["date"] for r in daily if r["date"] < end]
            start = prior[-1] + timedelta(days=1) if prior else end
        else:
            start = _start_for(label, spec, end, first) + timedelta(days=1)
        partial = start < first
        start = max(start, first)

        def last(series):
            pts = money_series(series, start) if series else []
            return pts[-1][1] if pts else None
        out.append({"label": label, "portfolio": last(daily), "benchmark": last(bench_daily) if bench_daily else None,
                    "partial": partial})
    return out
