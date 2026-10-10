from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import cash as cash_svc, goals as goals_svc, heatmap, importer, portfolio, prices, share as share_svc, watch
from services.pipeline import filings_for, get_company, search_companies
from services.portfolio_calc import (PERIODS, Series, allocation, daily_values, money_series, money_weighted_returns,
                                     period_returns, positions, shadow_benchmark,
                                     rebalance, twr_index)
from sources import configured_sources, get_source, visible_sources

from .portfolio_charts import donut_svg, performance_svg

YEARS = 4
BENCHMARKS = {"S&P 500 (SPY)": "SPY", "Nasdaq 100 (QQQ)": "QQQ", "No benchmark": None}
CHART_PERIODS = ["1W", "1M", "3M", "6M", "YTD", "1Y", "2Y", "3Y", "All"]


OTHER_LABEL = "Other (any Yahoo Finance symbol)"
MASK = "•••••"


@st.cache_data(ttl=3600, show_spinner=False)
def long_history(market: str, ticker: str) -> list:
    return portfolio.price_history(market, ticker, YEARS)


def _hidden() -> bool:
    return bool(st.session_state.get("pf-hide"))


def _shares(v: float) -> str:
    return MASK if _hidden() else f"{v:,.4g}"


@st.cache_data(ttl=3600, show_spinner=False)
def fx_history(currency: str) -> list:
    return prices.fx_history(currency, YEARS)


@st.cache_data(ttl=3600, show_spinner=False)
def bench_history(symbol: str) -> list:
    return prices.symbol_history(symbol, YEARS)


def _pct(v, signed: bool = True) -> str:
    return "–" if v is None else (f"{v * 100:+.2f}%" if signed else f"{v * 100:.1f}%")


def _cls(v) -> str:
    return "" if v is None else ("up" if v > 0.00005 else "down" if v < -0.00005 else "")


def _money(v, cur: str, signed: bool = False, always: bool = False) -> str:
    if v is None:
        return "–"
    if _hidden() and not always:
        return f"{MASK} {cur}"
    digits = 0 if cur in ("KRW", "JPY") or abs(v) >= 1000 else 2
    return f"{v:+,.{digits}f} {cur}" if signed else f"{v:,.{digits}f} {cur}"


def build(txs: list[dict], base: str, bench_symbol: str | None, cash_moves: list[dict] | None = None) -> dict:
    """Everything the page shows, in the base currency. With cash entries, cash is part of the value."""
    cash_moves = cash_moves or []
    pairs = sorted({(t["market"], t["ticker"]) for t in txs})
    currency_of = {p: portfolio.currency_for(*p) for p in pairs}
    with ThreadPoolExecutor(max_workers=8) as pool:
        hist = dict(zip(pairs, pool.map(lambda p: long_history(*p), pairs)))
    currencies = (set(currency_of.values()) | {m["currency"] for m in cash_moves} | ({base} if base != "USD" else set())) - {"USD"}
    fxs = {c: Series(fx_history(c)) for c in currencies}
    problems = []
    no_fx = {c for c in currencies if not fxs[c].values}
    if no_fx:
        problems.append("No exchange rate available for " + ", ".join(sorted(no_fx)) + ".")
    no_price = [p for p in pairs if not hist.get(p)]
    if no_price:
        problems.append("No price data for " + ", ".join(t for _, t in no_price) + "; left out of returns.")
    usable = [t for t in txs if (t["market"], t["ticker"]) not in no_price
              and currency_of[(t["market"], t["ticker"])] not in no_fx]
    base_fx = fxs.get(base) if base != "USD" else None
    if base != "USD" and base in no_fx:
        return {"problems": problems + [f"Can't show values in {base} right now."], "rows": [], "daily": []}

    def convert(amount: float, key: tuple[str, str], day: date) -> float | None:
        cur = currency_of.get(key, "USD")
        usd = amount if cur == "USD" else (amount * fxs[cur].at(day) if fxs[cur].at(day) else None)
        if usd is None:
            return None
        return usd if base == "USD" else (usd / base_fx.at(day) if base_fx and base_fx.at(day) else None)

    price_series = {p: Series(hist[p]) for p in pairs if hist.get(p)}
    bench_raw = bench_history(bench_symbol) if bench_symbol else []
    days = sorted({d for v in hist.values() for d, _ in v} | {d for d, _ in bench_raw})
    days = sorted(set(days) | {m["move_date"] for m in cash_moves})
    daily = daily_values(usable, price_series, fxs, currency_of, base, days, base_fx,
                         cash_moves=[m for m in cash_moves if m["currency"] not in no_fx])
    index = twr_index(daily)
    bench_base = None
    if bench_raw:
        pts = [(d, v if base == "USD" else (v / base_fx.at(d) if base_fx and base_fx.at(d) else None)) for d, v in bench_raw]
        bench_base = Series([(d, v) for d, v in pts if v])
    today = days[-1] if days else date.today()
    pos = positions(usable, convert=convert)
    rows = []
    for key, p in pos.items():
        if p["shares"] <= 1e-9:
            continue
        h = hist.get(key) or []
        last = h[-1][1] if h else None
        if key[0] == portfolio.OTHER:
            info = portfolio.symbol_info(key[1]) or {}
            name, country = info.get("name") or key[1], info.get("country") or "Other"
        else:
            comp = get_company(*key, resolve=False) or {}
            src = get_source(key[0])
            name, country = comp.get("name_en") or key[1], src.country if src else key[0]
        price_base = convert(last, key, today) if last is not None else None
        rows.append({"market": key[0], "ticker": key[1], "name": name,
                     "country": country, "currency": currency_of[key],
                     "shares": p["shares"], "avg": p["avg"], "last": last, "day": prices.last_move(h) if h else None,
                     "price_base": price_base, "value_base": p["shares"] * price_base if price_base is not None else None,
                     "cost_base": p["cost_base"], "realized_base": p["realized_base"]})
    if cash_moves:
        balances: dict[str, float] = {}
        for m in cash_moves:
            sign = -1.0 if m["kind"] in ("withdraw", "fee") else 1.0
            balances[m["currency"]] = balances.get(m["currency"], 0.0) + sign * m["amount"]
        for t in usable:
            cur = currency_of[(t["market"], t["ticker"])]
            fees = t.get("fees") or 0
            balances[cur] = balances.get(cur, 0.0) + (-(t["shares"] * t["price"] + fees) if t["kind"] == "buy"
                                                      else t["shares"] * t["price"] - fees)
        for cur, amount in sorted(balances.items()):
            if abs(amount) < 0.005 or cur in no_fx:
                continue
            currency_of[("CASH", cur)] = cur
            rows.append({"market": "CASH", "ticker": cur, "name": f"Cash ({cur})", "country": "Cash", "currency": cur,
                         "shares": amount, "avg": 1.0, "last": 1.0, "day": 0.0, "price_base": None,
                         "value_base": convert(amount, ("CASH", cur), today), "cost_base": convert(amount, ("CASH", cur), today) or 0.0,
                         "realized_base": 0.0, "is_cash": True})
        if any(r.get("is_cash") and (r["value_base"] or 0) < 0 for r in rows):
            problems.append("A cash balance is negative: add the deposits that paid for your purchases on the Cash tab.")
    total = sum(r["value_base"] or 0 for r in rows)
    for r in rows:
        r["weight"] = (r["value_base"] or 0) / total if total else 0
        r["gain_base"] = (r["value_base"] - r["cost_base"]) if r["value_base"] is not None else None
        r["gain_pct"] = (r["value_base"] / r["cost_base"] - 1) if r["value_base"] is not None and r["cost_base"] else None
    realized = sum(p["realized_base"] for p in pos.values())
    cost = sum(r["cost_base"] for r in rows)
    today_change = (daily[-1]["value"] - daily[-2]["value"] - daily[-1]["flow"]) if len(daily) >= 2 else None
    rows.sort(key=lambda r: (bool(r.get("is_cash")), -(r["value_base"] or 0)))
    shadow = shadow_benchmark(daily, bench_base) if bench_base and daily else []
    return {"rows": rows, "daily": daily, "index": index, "bench": bench_base, "problems": problems, "shadow": shadow,
            "returns": period_returns(index, bench_base) if index else [],
            "money_returns": money_weighted_returns(daily, shadow or None) if daily else [], "total": total, "cost": cost,
            "realized": realized, "today_change": today_change, "convert": convert}


@st.cache_data(ttl=600, show_spinner=False)
def quick_summary(uid: int, base: str, signature: str) -> dict | None:
    """Portfolio value and today's move for the Today page header (cached for 10 minutes or until trades change)."""
    txs = portfolio.list_transactions(uid)
    if not txs:
        return None
    model = build(txs, base, None)
    if not model["rows"]:
        return None
    return {"value": model["total"], "today": model["today_change"],
            "today_pct": model["money_returns"][0]["portfolio"] if model["money_returns"] else None,
            "week_pct": model["money_returns"][1]["portfolio"] if len(model["money_returns"]) > 1 else None,
            "gain": model["total"] - model["cost"] + model["realized"], "cost": model["cost"]}


def page() -> None:
    user = st.session_state["user"]
    uid = user["id"]
    page_header("Portfolio", "Returns, allocation and rebalancing for your holdings, with price alerts.")
    txs = portfolio.list_transactions(uid)
    home = portfolio.home_currency(uid)

    st.session_state["pf-hide"] = portfolio.hide_amounts(uid)
    c1, c2, c3, c4 = st.columns([1.2, 1, 1.3, 0.9], vertical_alignment="bottom")
    mode = c1.segmented_control("Show values in", ["USD", "Home currency"], default="USD", key="pf-mode") or "USD"
    c4.toggle("Hide amounts", key="pf-hide", help="Hides money values and share counts; percentages stay visible.",
              on_change=lambda: portfolio.set_hide_amounts(uid, st.session_state["pf-hide"]))
    chosen = c2.selectbox("Home currency", portfolio.HOME_CURRENCIES,
                          index=portfolio.HOME_CURRENCIES.index(home) if home in portfolio.HOME_CURRENCIES else 0)
    if chosen != home:
        portfolio.set_home_currency(uid, chosen)
        home = chosen
    bench_label = c3.selectbox("Benchmark", list(BENCHMARKS), key="pf-bench")
    base = "USD" if mode == "USD" else home
    bench_symbol = BENCHMARKS[bench_label]

    t_over, t_hold, t_cash, t_reb, t_tx, t_goal, t_alert, t_rep = st.tabs(
        ["Overview", "Holdings", "Cash", "Rebalance", "Transactions", "Goals", "Price alerts", "Report & share"])
    model = None
    if txs:
        with st.spinner("Calculating returns"):
            model = build(txs, base, bench_symbol, cash_svc.list_moves(uid))
        for p in model["problems"]:
            st.caption(p)

    with t_over:
        if not model or not model["rows"]:
            html_block('<div class="empty">No holdings yet. Add your buys in the Transactions tab to see returns, '
                       'allocation and rebalancing.</div>')
        else:
            overview(model, base, bench_symbol)
    with t_hold:
        if model and model["rows"]:
            holdings_table(model, base, uid)
        else:
            st.caption("No holdings yet.")
    with t_reb:
        if model and model["rows"]:
            rebalance_tab(model, base, uid)
        else:
            st.caption("Add holdings first.")
    with t_tx:
        transactions_tab(user, txs)
    with t_cash:
        cash_tab(user, model, base)
    with t_goal:
        goals_tab(user, model, base)
    with t_alert:
        alerts_tab(user)
    with t_rep:
        report_tab(user, model)


def overview(model: dict, base: str, bench_symbol: str | None) -> None:
    c1, c2 = st.columns([1.3, 2], vertical_alignment="center")
    method = c1.segmented_control("Returns", ["On your money", "Time-weighted"], default="On your money", key="pf-method",
                                  help="On your money: gain divided by the money you had in, so it matches your total gain. "
                                       "Time-weighted: how the investments performed regardless of when money was added; "
                                       "it can look very high if early, small holdings rose a lot.") or "On your money"
    period = c2.segmented_control("Chart period", CHART_PERIODS, default="1Y", key="pf-period",
                                  label_visibility="collapsed") or "1Y"
    index, daily = model["index"], model["daily"]
    end = index[-1][0]
    start = {"1W": end - timedelta(days=7), "1M": end - timedelta(days=30), "3M": end - timedelta(days=91), "6M": end - timedelta(days=182),
             "YTD": date(end.year, 1, 1), "1Y": end - timedelta(days=365), "2Y": end - timedelta(days=730),
             "3Y": end - timedelta(days=1095), "All": index[0][0]}[period]
    if method == "On your money":
        part = [(d, 1 + r) for d, r in money_series(daily, max(start, daily[0]["date"]))]
        bench = [(d, 1 + r) for d, r in money_series(model["shadow"], max(start, daily[0]["date"]))] if model["shadow"] else []
        if part:
            part = [(part[0][0] - timedelta(days=1), 1.0)] + part
            bench = ([(bench[0][0] - timedelta(days=1), 1.0)] + bench) if bench else []
    else:
        part = [(d, v) for d, v in index if d >= start]
        bench = [(d, model["bench"].at(d)) for d, _ in part] if model["bench"] else []
    if len(part) >= 2:
        html_block(performance_svg(part, [(d, v) for d, v in bench if v], bench_symbol or ""))
    else:
        st.caption("Not enough history for this period yet.")

    tiles = []
    for r in (model["money_returns"] if method == "On your money" else model["returns"]):
        b = (f'<div class="b">{esc(bench_symbol)} <span class="{_cls(r["benchmark"])}">{_pct(r["benchmark"])}</span></div>'
             if bench_symbol and r["benchmark"] is not None else "")
        note = '<div class="note">since first buy</div>' if r["partial"] else ""
        tiles.append(f'<div class="ret"><div class="lbl">{r["label"]}</div>'
                     f'<div class="p {_cls(r["portfolio"])}">{_pct(r["portfolio"])}</div>{b}{note}</div>')
    html_block('<div class="ret-grid">' + "".join(tiles) + "</div>")
    if method == "On your money":
        st.caption(f"Return on your money in {base}: the gain in each period divided by what you had invested at its start "
                   "plus money added during it. 'All' equals your total gain. "
                   + (f"{bench_symbol} shows what the same money, put in on the same days, would have made." if bench_symbol else ""))
    else:
        st.caption(f"Time-weighted returns in {base}: how the investments performed, ignoring when money was added. They can "
                   "be much higher than your total gain if early, smaller holdings rose a lot. "
                   + (f"{bench_symbol} is its price return over the same periods." if bench_symbol else ""))

    total, cost, realized = model["total"], model["cost"], model["realized"]
    gain = total - sum(r["flow"] for r in model["daily"])
    added = sum(max(r["flow"], 0.0) for r in model["daily"])
    stats = [("Value", _money(total, base), ""),
             ("Total gain", _money(gain, base, True), f'{_pct(gain / added if added else None)} on {_money(added, base)} put in'),
             ("Today", _money(model["today_change"], base, True) if model["today_change"] is not None else "–",
              _pct(model["money_returns"][0]["portfolio"]) if model["money_returns"] else ""),
             ("Realised gains", _money(realized, base, True), "from sales")]
    html_block('<div class="stat-grid">' + "".join(
        f'<div class="stat"><div class="k">{k}</div><div class="v">{esc(v)}</div><div class="k">{esc(sub)}</div></div>'
        for k, v, sub in stats) + "</div>")

    rows = model["rows"]
    heat = heatmap.svg([{"name": r["name"], "ticker": r["ticker"], "value": r["value_base"], "move": r["day"]}
                        for r in rows if not r.get("is_cash")])
    if heat:
        st.caption("Today's moves: size shows each holding's value, colour its move today.")
        html_block(heat)
    html_block('<div class="alloc-grid">' + donut_svg("By company", allocation(rows, "name"))
               + donut_svg("By country", allocation(rows, "country"))
               + donut_svg("By currency", allocation(rows, "currency")) + "</div>")


def holdings_table(model: dict, base: str, uid: int) -> None:
    lines = []
    for r in model["rows"]:
        if r.get("is_cash"):
            lines.append(f'<tr><td><span class="logo" style="background:#5F6B7A">$</span>{esc(r["name"])}</td>'
                         f'<td class="num">–</td><td class="num opt">–</td><td class="num">{_money(r["shares"], r["currency"])}</td>'
                         f'<td class="num">{_money(r["value_base"], base)}</td><td class="num opt">{r["weight"] * 100:.1f}%</td>'
                         f'<td class="num">–</td><td class="num opt">–</td><td class="num opt"></td></tr>')
            continue
        src = get_source(r["market"])
        recent = len(filings_for([(r["market"], r["ticker"])], src.today() - timedelta(days=7))) if src else ""
        lines.append(
            f'<tr><td>{logo_html(r["name"], r["ticker"], market=r["market"])}{esc(r["name"])} <span class="fl-tk">{esc(r["ticker"])}</span></td>'
            f'<td class="num">{_shares(r["shares"])}</td><td class="num opt">{_money(r["avg"], r["currency"], always=True)}</td>'
            f'<td class="num">{_money(r["last"], r["currency"], always=True)}</td><td class="num">{_money(r["value_base"], base)}</td>'
            f'<td class="num opt">{r["weight"] * 100:.1f}%</td>'
            f'<td class="num"><span class="mv {_cls(r["gain_base"])}">{_money(r["gain_base"], base, True)}'
            f' ({_pct(r["gain_pct"])})</span></td><td class="num opt">{prices.move_html(r["day"])}</td>'
            f'<td class="num opt">{recent or ""}</td></tr>')
    html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>Company</th><th class="num">Shares</th>'
               '<th class="num opt">Avg cost</th><th class="num">Last price</th><th class="num">Value</th>'
               '<th class="num opt">Weight</th><th class="num">Gain</th><th class="num opt">Today</th>'
               f'<th class="num opt">Filings 7d</th></tr>{"".join(lines)}</table></div>')
    st.caption(f"Cost and last price in each stock's own currency; value and gain in {base}, with cost converted at "
               "the exchange rate on each purchase date (so gains include currency moves). Prices may be delayed.")


def rebalance_tab(model: dict, base: str, uid: int) -> None:
    held_cash = sum(r["value_base"] or 0 for r in model["rows"] if r.get("is_cash"))
    rows = [r for r in model["rows"] if not r.get("is_cash")]
    saved = portfolio.get_targets(uid)
    st.caption("Set a target weight for each holding (they should add up to 100%). Leave a target empty to leave "
               "that holding alone. Add new cash to see how to invest it.")
    frame = pd.DataFrame([{"Company": f'{r["name"]} ({r["ticker"]})', "Current %": round(r["weight"] * 100, 2),
                           "Target %": saved.get((r["market"], r["ticker"]))} for r in rows])
    edited = st.data_editor(frame, hide_index=True, width="stretch", key="pf-targets",
                            disabled=["Company", "Current %"],
                            column_config={"Target %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0,
                                                                                     step=0.5, format="%.1f")})
    c1, c2 = st.columns([1, 1], vertical_alignment="bottom")
    cash = c1.number_input(f"New cash to invest ({base})", min_value=0.0, value=max(0.0, round(held_cash, 2)), step=100.0,
                           help="Starts at the cash you hold (from the Cash tab).")
    targets = {(r["market"], r["ticker"]): (None if pd.isna(v) else float(v))
               for r, v in zip(rows, edited["Target %"].tolist())}
    if c2.button("Save targets", width="stretch"):
        portfolio.save_targets(uid, targets)
        st.toast("Targets saved")
    set_targets = {k: v for k, v in targets.items() if v is not None}
    total_target = sum(set_targets.values())
    if not set_targets:
        return
    if abs(total_target - 100) > 0.05:
        st.warning(f"Targets add up to {total_target:.1f}%, not 100%. Suggestions below still follow the targets as set.")
    plan = rebalance(rows, set_targets, cash)
    lines = []
    for r in plan:
        if r["target"] is None:
            continue
        action = "buy" if r["trade_value"] > 0.005 else "sell" if r["trade_value"] < -0.005 else ""
        shares = abs(r["trade_shares"])
        lines.append(f'<tr><td>{esc(r["name"])} <span class="fl-tk">{esc(r["ticker"])}</span></td>'
                     f'<td class="num">{r["current"] * 100:.1f}%</td><td class="num">{r["target"] * 100:.1f}%</td>'
                     f'<td class="num">{(r["target"] - r["current"]) * 100:+.1f}%</td>'
                     f'<td class="num {action}">{_money(r["trade_value"], base, True)}</td>'
                     f'<td class="num {action}">{(action.capitalize() + " " + (MASK if _hidden() else f"{shares:,.2f}")) if action else "–"}</td>'
                     f'<td class="num">{_money(r["last"], r["currency"], always=True)}</td></tr>')
    html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>Company</th><th class="num">Now</th><th class="num">Target</th>'
               '<th class="num">Gap</th><th class="num">Trade value</th><th class="num">Shares</th><th class="num">At price</th></tr>'
               f'{"".join(lines)}</table></div>')
    st.caption("Share counts use the last price and today's exchange rate, and ignore fees and taxes. Round to whole "
               "shares where your broker requires it. Not investment advice.")


def _resolve(market: str, query: str) -> dict | None:
    if market == portfolio.OTHER:
        info, err = portfolio.add_symbol(query)
        if err:
            st.warning(err)
            return None
        return {"ticker": info["symbol"], "name_en": info["name"]}
    src = get_source(market)
    ticker = src.normalize_ticker(query)
    comp = get_company(market, ticker) if ticker else None
    if not comp:
        found = search_companies(market, query)
        comp = get_company(market, found[0].ticker) if len(found) == 1 else None
    return comp


def _market_close(market: str, ticker: str, day: date) -> float | None:
    hist = long_history(market, ticker)
    return Series(hist).at(day) if hist else None


def _checked_price(market: str, ticker: str, day: date, price: float, confirmed: bool) -> tuple[float | None, str | None]:
    """Catch prices far from the market price; convert Israeli prices typed in agorot to shekels."""
    close = _market_close(market, ticker, day)
    if not close or not price:
        return price, None
    ratio = price / close
    if market == "IL" and 60 <= ratio <= 160:
        st.toast(f"Converted {price:,.2f} agorot to {price / 100:,.2f} shekels")
        return price / 100, None
    if (ratio > 3 or ratio < 1 / 3) and not confirmed:
        return None, (f"{price:,.4g} is far from the market price that day (about {close:,.4g} "
                      f"{portfolio.currency_for(market, ticker)}). Check the price and currency, then tick "
                      "\"The price is correct\" to save anyway.")
    return price, None


def _close_on(market: str, ticker: str, day) -> float | None:
    hist = long_history(market, ticker)
    before = [v for d, v in hist if d <= day]
    return before[-1] if before else None


def _checked_price(market: str, ticker: str, day, price: float, confirmed: bool) -> tuple[float | None, str | None]:
    """(price to save, message). price is None when the user should check it first."""
    close = _close_on(market, ticker, day)
    if close and price > 0:
        ratio = price / close
        if market == "IL" and 50 <= ratio <= 200:
            return price / 100, f"Converted {price:,.4g} agorot to {price / 100:,.4g} shekels (TASE shows prices in agorot)."
        if market in ("IL", "UK") and 0.005 <= ratio <= 0.02 and not confirmed:
            return None, (f"The market closed at about {close:,.4g} on that date, so {price:,.4g} looks 100 times too small. "
                          f"Did you mean {price * 100:,.4g}? Enter that, or tick \"The price is correct\" to keep {price:,.4g}.")
        if (ratio > 3 or ratio < 1 / 3) and not confirmed:
            return None, (f"The market closed at about {close:,.4g} on that date, so {price:,.4g} looks off. Check it is "
                          "the price per share in the stock's own currency, or tick \"The price is correct\" to save anyway.")
    return price, None


def transactions_tab(user: dict, txs: list[dict]) -> None:
    uid = user["id"]
    markets = {**{s.country: s.market for s in visible_sources(user)}, OTHER_LABEL: portfolio.OTHER}
    with st.form("tx-add", clear_on_submit=True, border=True):
        st.markdown("**Add a buy or sell**")
        c1, c2, c3 = st.columns([1.1, 1.6, 0.9])
        country = c1.selectbox("Country", list(markets))
        query = c2.text_input("Ticker or name", placeholder="e.g. 005930, AAPL, EOS, or RELIANCE.NS for Other",
                              help="For Other, use the symbol from finance.yahoo.com, e.g. RELIANCE.NS (India), "
                                   "VOD.L (London), SAP.DE (Germany), 0700.HK (Hong Kong), SHOP.TO (Canada).")
        kind = c3.selectbox("Type", ["Buy", "Sell"])
        c4, c5, c6, c7 = st.columns(4)
        day = c4.date_input("Date", value=date.today(), max_value=date.today())
        shares = c5.number_input("Shares", min_value=0.0, step=1.0, format="%.4g")
        price = c6.number_input("Price per share", min_value=0.0, step=0.01, format="%.4f",
                                help="In the stock's own currency, e.g. AUD for Australian shares, shekels for Israel "
                                     "(agorot are converted automatically).")
        fees = c7.number_input("Fees", min_value=0.0, step=1.0, format="%.2f")
        c8, c9 = st.columns(2)
        also = c8.checkbox("Also follow its filings (not available for Other)", value=True)
        confirmed = c9.checkbox("The price is correct (skip the price check)")
        if st.form_submit_button("Save", type="primary"):
            market = markets[country]
            comp = _resolve(market, query)
            if not comp and market != portfolio.OTHER:
                st.warning("Couldn't find that company. Use its ticker, e.g. 005930, AAPL or EOS.")
            elif comp:
                use, message = _checked_price(market, comp["ticker"], day, price, confirmed)
                if use is None:
                    st.warning(message)
                else:
                    err = portfolio.add_transaction(uid, market, comp["ticker"], kind.lower(), day, shares, use, fees)
                    if err:
                        st.warning(err)
                    else:
                        if also and kind == "Buy" and market != portfolio.OTHER and not watch.is_watching(uid, market, comp["ticker"]):
                            watch.add(user, market, comp["ticker"])
                        if message:
                            st.session_state["pf-note"] = message
                        st.toast(f"Saved {kind.lower()} of {comp['name_en']}")
                        st.rerun()
    import_section(user, markets)
    if st.session_state.get("pf-note"):
        st.info(st.session_state.pop("pf-note"))
    if not txs:
        st.caption("No transactions yet.")
        return
    st.caption("Holdings saved before transactions existed were turned into buys dated the day you added them. "
               "Edit them to set the real purchase date and price.")
    names = {}
    editing = st.session_state.get("pf-edit")
    for t in txs[:200]:
        key = (t["market"], t["ticker"])
        if key not in names:
            names[key] = ((portfolio.symbol_info(t["ticker"]) or {}).get("name") if t["market"] == portfolio.OTHER
                          else (get_company(*key, resolve=False) or {}).get("name_en")) or t["ticker"]
        cur = portfolio.currency_for(*key)
        close = _close_on(*key, t["tx_date"])
        odd = bool(close and t["price"] and not (1 / 3 <= t["price"] / close <= 3))
        flag = (f' <span class="fl-flag">⚠ price looks off (market about {close:,.4g})</span>' if odd and not _hidden()
                else ' <span class="fl-flag">⚠ price looks off</span>' if odd else "")
        fee_text = (f', fees {MASK if _hidden() else f"{t['fees']:,.2f}"}' if t["fees"] else "")
        price_text = MASK if _hidden() else f'{t["price"]:,.4g}'
        hundredths = t["market"] in ("IL", "UK") or (t["market"] == portfolio.OTHER and t["ticker"].endswith((".TA", ".L")))
        factor = None
        if odd and hundredths and close:
            ratio = t["price"] / close
            factor = 100.0 if 0.005 <= ratio <= 0.02 else 0.01 if 50 <= ratio <= 200 else None
        c1, c2, c3 = st.columns([6, 0.8, 0.8], vertical_alignment="center")
        if factor:
            fixed = t["price"] * factor
            if c1.button(f"Fix {'×' if factor > 1 else '÷'}100: use {fixed:,.2f} {cur}", key=f"tx-fix-{t['id']}", type="secondary"):
                err = portfolio.update_transaction(uid, t["id"], t["kind"], t["tx_date"], t["shares"], fixed, t["fees"] or 0)
                st.session_state["pf-note"] = err or f"Price corrected to {fixed:,.2f} {cur}."
                st.rerun()
        c1.markdown(f'{t["tx_date"]:%d %b %Y}  **{t["kind"].capitalize()}** {_shares(t["shares"])} × '
                    f'{esc(names[key])} ({esc(t["ticker"])}) at {price_text} {cur}{fee_text}{flag}', unsafe_allow_html=True)
        if c2.button("Edit", key=f"tx-edit-{t['id']}", type="tertiary"):
            st.session_state["pf-edit"] = t["id"]
            st.rerun()
        if c3.button("Delete", key=f"tx-del-{t['id']}", type="tertiary"):
            portfolio.remove_transaction(uid, t["id"])
            st.rerun()
        if editing == t["id"]:
            with st.form(f"tx-edit-form-{t['id']}", border=True):
                st.markdown(f"**Edit {esc(names[key])} ({esc(t['ticker'])})**")
                e1, e2, e3, e4, e5 = st.columns([0.9, 1.1, 1, 1, 0.9])
                new_kind = e1.selectbox("Type", ["Buy", "Sell"], index=0 if t["kind"] == "buy" else 1)
                new_day = e2.date_input("Date", value=t["tx_date"], max_value=date.today())
                new_shares = e3.number_input("Shares", min_value=0.0, value=float(t["shares"]), step=1.0, format="%.4g")
                new_price = e4.number_input(f"Price ({cur})", min_value=0.0, value=float(t["price"]), step=0.01, format="%.4f")
                new_fees = e5.number_input("Fees", min_value=0.0, value=float(t["fees"] or 0), step=1.0, format="%.2f")
                ok_price = st.checkbox("The price is correct (skip the price check)", key=f"tx-ok-{t['id']}")
                b1, b2, _ = st.columns([1, 1, 4])
                save = b1.form_submit_button("Save changes", type="primary")
                cancel = b2.form_submit_button("Cancel")
            if cancel:
                st.session_state.pop("pf-edit", None)
                st.rerun()
            if save:
                use, message = _checked_price(t["market"], t["ticker"], new_day, new_price, ok_price)
                if use is None:
                    st.warning(message)
                else:
                    err = portfolio.update_transaction(uid, t["id"], new_kind.lower(), new_day, new_shares, use, new_fees)
                    if err:
                        st.warning(err)
                    else:
                        st.session_state.pop("pf-edit", None)
                        if message:
                            st.session_state["pf-note"] = message
                        st.toast("Transaction updated")
                        st.rerun()


def _import_resolver(market: str, symbol: str) -> dict | None:
    if market == portfolio.OTHER:
        info, _ = portfolio.add_symbol(symbol)
        return {"ticker": info["symbol"], "name_en": info["name"]} if info else None
    src = get_source(market)
    if not src:
        return None
    ticker = src.normalize_ticker(symbol)
    comp = get_company(market, ticker) if ticker else None
    if not comp:
        found = search_companies(market, symbol)
        comp = get_company(market, found[0].ticker) if len(found) == 1 else None
    return comp


def import_section(user: dict, markets: dict) -> None:
    with st.expander("Import from a broker file (CSV)", icon=":material/upload_file:"):
        st.caption("Export your trades from your broker as a CSV file and upload it here. You'll see every row checked "
                   "before anything is saved, and rows already in your transactions are skipped.")
        up = st.file_uploader("Broker CSV file", type=["csv", "txt"], key="imp-file")
        if not up:
            return
        try:
            frame = importer.read(up.getvalue())
        except Exception:
            st.error("Couldn't read that file. Save it as CSV (comma or semicolon separated) and try again.")
            return
        file_key = f"{up.name}:{up.size}"
        if st.session_state.get("imp-key") != file_key:
            st.session_state["imp-key"] = file_key
            st.session_state.pop("imp-plan", None)
        st.caption(f"{len(frame)} rows found. Check the column matches below.")
        guess = importer.guess_columns(list(frame.columns))
        options = ["(none)"] + list(frame.columns)
        labels = {"date": "Date", "symbol": "Symbol or ticker", "action": "Buy / sell", "shares": "Quantity",
                  "price": "Price per share", "fees": "Fees (optional)", "market": "Market (optional)"}
        cols = st.columns(4)
        mapping = {}
        for i, (field, label) in enumerate(labels.items()):
            chosen = cols[i % 4].selectbox(label, options, index=options.index(guess[field]) if guess.get(field) else 0,
                                           key=f"imp-col-{field}")
            mapping[field] = None if chosen == "(none)" else chosen
        c1, c2 = st.columns(2)
        default_country = c1.selectbox("Market for rows without one", list(markets), key="imp-market",
                                       help="Used when the file has no market column, or a market the app doesn't know.")
        dates = frame[mapping["date"]].tolist()[:50] if mapping.get("date") else []
        order = c2.selectbox("Dates are written", ["Day first (31/12/2025)", "Month first (12/31/2025)"],
                             index=0 if importer.day_first_guess(dates) else 1, key="imp-order")
        if st.button("Check rows", key="imp-check"):
            with st.spinner("Checking each row"):
                st.session_state["imp-plan"] = importer.plan(frame, mapping, markets[default_country],
                                                             order.startswith("Day"), user["id"], _import_resolver)
        rows = st.session_state.get("imp-plan")
        if not rows:
            return
        counts = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        st.markdown("   ".join(f"**{counts[k]}** {k}" for k in ("ready", "warning", "duplicate", "problem", "skip") if k in counts))
        table = pd.DataFrame([{"Line": r["line"], "Status": r["status"], "Date": r.get("date"), "Symbol": r.get("symbol"),
                               "Company": r.get("name", ""), "Type": r.get("kind") or "",
                               "Quantity": r.get("shares"), "Price": r.get("price"), "Fees": r.get("fees"),
                               "Note": r.get("note", "")} for r in rows])
        st.dataframe(table, hide_index=True, width="stretch", height=min(400, 38 + 35 * len(rows)))
        include = st.checkbox("Also import rows with warnings (unusual prices)", key="imp-warn")
        n = counts.get("ready", 0) + (counts.get("warning", 0) if include else 0)
        if st.button(f"Import {n} row{'s' if n != 1 else ''}", type="primary", disabled=n == 0, key="imp-go"):
            saved, errors = importer.save(rows, user["id"], include)
            st.session_state.pop("imp-plan", None)
            st.session_state["pf-note"] = f"Imported {saved} transaction{'s' if saved != 1 else ''}." + (
                " Problems: " + "; ".join(errors[:5]) if errors else "")
            st.rerun()


def alerts_tab(user: dict) -> None:
    st.caption("Sent to Telegram at the next check (about every 10 minutes). Price above / below alerts fire once, "
               "then switch off; daily-move alerts can fire once a day per stock.")
    wl = watch.get_watchlist(user["id"])
    choices = [("*", "*", "Any company on my watchlist")] + [(w["market"], w["ticker"], f'{w["name_en"]} ({w["ticker"]})') for w in wl]
    with st.form("price-alert", clear_on_submit=True, border=False):
        c1, c2, c3, c4 = st.columns([1.8, 1.4, 0.9, 0.7], vertical_alignment="bottom")
        pick = c1.selectbox("Company", range(len(choices)), format_func=lambda i: choices[i][2])
        kind = c2.selectbox("When", list(portfolio.KINDS), format_func=lambda k: portfolio.KINDS[k])
        value = c3.number_input("% or price", min_value=0.0, value=5.0, step=0.5)
        if c4.form_submit_button("Add", width="stretch"):
            m, t, _ = choices[pick]
            if m == "*" and kind != "move":
                st.warning("For the whole watchlist, use a daily-move alert.")
            else:
                err = portfolio.add_alert(user["id"], m, t, kind, value)
                st.warning(err) if err else st.rerun()
    alerts = portfolio.list_alerts(user["id"])
    if not alerts:
        st.caption("No price alerts yet.")
    for a in alerts:
        who = "Any watchlist company" if a["ticker"] == "*" else a["ticker"]
        what = f"moves {a['value']:g}% or more in a day" if a["kind"] == "move" else \
            f"{'rises above' if a['kind'] == 'above' else 'falls below'} {a['value']:,.2f}"
        status = "" if a["active"] else '<span class="fl-tk">triggered</span>'
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.markdown(f"**{esc(who)}** {esc(what)} {status}", unsafe_allow_html=True)
        if c2.button("Remove", key=f"pa-{a['id']}", type="tertiary"):
            portfolio.remove_alert(user["id"], a["id"])
            st.rerun()



def cash_tab(user: dict, model: dict | None, base: str) -> None:
    st.caption("Record deposits, withdrawals, dividends received, interest and fees. Once you add cash entries, your "
               "value includes cash, buys and sells move money between cash and shares, and returns are measured on "
               "the money you put in or took out.")
    if model:
        cash_rows = [r for r in model["rows"] if r.get("is_cash")]
        if cash_rows:
            html_block('<div class="stat-grid">' + "".join(
                f'<div class="stat"><div class="k">{esc(r["name"])}</div><div class="v">{esc(_money(r["shares"], r["currency"]))}</div>'
                f'<div class="k">{esc(_money(r["value_base"], base))}</div></div>' for r in cash_rows) + "</div>")
    with st.form("cash-add", clear_on_submit=True, border=True):
        c1, c2, c3, c4 = st.columns([1.1, 1.2, 1, 0.8])
        day = c1.date_input("Date", value=date.today(), max_value=date.today())
        kind = c2.selectbox("Type", list(cash_svc.KINDS), format_func=lambda k: cash_svc.KINDS[k])
        amount = c3.number_input("Amount", min_value=0.0, step=100.0, format="%.2f")
        currency = c4.selectbox("Currency", sorted(set(portfolio.CURRENCY.values()) | set(portfolio.HOME_CURRENCIES)), index=0)
        note = st.text_input("Note (optional)")
        if st.form_submit_button("Add", type="primary"):
            err = cash_svc.add(user["id"], day, kind, amount, currency, note)
            st.warning(err) if err else st.rerun()
    for m in cash_svc.list_moves(user["id"])[:200]:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        sign = "-" if m["kind"] in ("withdraw", "fee") else "+"
        amount = MASK if _hidden() else f"{sign}{m['amount']:,.2f}"
        c1.markdown(f'{m["move_date"]:%d %b %Y}  **{cash_svc.KINDS[m["kind"]]}** {amount} {m["currency"]}'
                    + (f"  {esc(m['note'])}" if m["note"] else ""), unsafe_allow_html=True)
        if c2.button("Delete", key=f"cash-del-{m['id']}", type="tertiary"):
            cash_svc.remove(user["id"], m["id"])
            st.rerun()


def goals_tab(user: dict, model: dict | None, base: str) -> None:
    saved = goals_svc.get(user["id"]) or {}
    current = model["total"] if model and model.get("rows") else 0.0
    with st.form("goal", border=True):
        c1, c2, c3, c4 = st.columns(4)
        target = c1.number_input(f"Target ({base})", min_value=0.0, value=float(saved.get("target") or max(current * 2, 100000.0)), step=10000.0)
        when = c2.date_input("By", value=saved.get("target_date") or date(date.today().year + 10, 12, 31), min_value=date.today())
        monthly = c3.number_input(f"Monthly saving ({base})", min_value=0.0, value=float(saved.get("monthly") or 0), step=100.0)
        expected = c4.number_input("Expected return % a year", min_value=-20.0, max_value=40.0,
                                   value=float((saved.get("expected_return") or 0.07) * 100), step=0.5)
        if st.form_submit_button("Save goal", type="primary"):
            goals_svc.save(user["id"], target, when, monthly, expected / 100, base)
            st.rerun()
    if not saved:
        st.caption("Set a target to see a projection.")
        return
    months = goals_svc.months_between(date.today(), saved["target_date"])
    path = goals_svc.project(current, saved["monthly"], saved["expected_return"], months)
    need_m = goals_svc.needed_monthly(current, saved["target"], saved["expected_return"], months)
    need_r = goals_svc.needed_return(current, saved["target"], saved["monthly"], months)
    on_track = path[-1] >= saved["target"]
    stats = [("Today", _money(current, base)), ("Projected", _money(path[-1], base)),
             ("Monthly saving needed", _money(need_m, base)), ("Return needed", "out of reach" if need_r is None else f"{need_r * 100:.1f}% a year")]
    html_block('<div class="stat-grid">' + "".join(f'<div class="stat"><div class="k">{k}</div><div class="v">{esc(v)}</div></div>'
                                                   for k, v in stats) + "</div>")
    st.markdown(("On track: the projection reaches your target." if on_track else
                 "Not on track yet: see the monthly saving or return needed above.") +
                f" ({months} months, {saved['expected_return'] * 100:.1f}% a year, {_money(saved['monthly'], base, always=True)} a month)")
    html_block(goals_svc.chart_svg(path, saved["target"], ("Today", f"{saved['target_date']:%b %Y}")))
    st.caption("A simple projection with a steady return; real markets go up and down. Not investment advice.")


def report_tab(user: dict, model: dict | None) -> None:
    st.markdown("**Monthly PDF report**")
    st.caption("Sent on the 1st of each month (Telegram and/or email, see Account). You can also make one now.")
    if st.button("Make this month's report", icon=":material/picture_as_pdf:"):
        from services.digests import monthly_pdf
        with st.spinner("Building the report"):
            made = monthly_pdf(user["id"])
        if made:
            st.download_button("Download PDF", made[1], file_name=made[0], mime="application/pdf", type="primary")
        else:
            st.warning("Add some holdings first.")
    st.divider()
    st.markdown("**Read-only share link**")
    st.caption("Anyone with the link sees your returns in %, allocation and holdings by weight. Never amounts, share "
               "counts or prices paid. Remove the link any time.")
    token = share_svc.get_token(user["id"])
    from core.config import get_secret
    app_url = (get_secret("APP_URL") or "").rstrip("/")
    c1, c2 = st.columns([1, 1])
    if token:
        st.code(f"{app_url}/?share={token}" if app_url else f"?share={token}", language=None)
        if c1.button("New link (old one stops working)"):
            share_svc.create(user["id"])
            st.rerun()
        if c2.button("Remove link"):
            share_svc.revoke(user["id"])
            st.rerun()
    elif c1.button("Create share link", type="primary"):
        share_svc.create(user["id"])
        st.rerun()


def share_page(user_id: int) -> None:
    """Public read-only view: percentages only."""
    from services import cash as cash_svc2
    txs = portfolio.list_transactions(user_id)
    page_header("Shared portfolio", "Returns and allocation only. Amounts are private.")
    if not txs:
        st.caption("Nothing to show yet.")
        return
    st.session_state["pf-hide"] = True
    model = build(txs, "USD", "SPY", cash_svc2.list_moves(user_id))
    if not model["rows"]:
        st.caption("Nothing to show yet.")
        return
    tiles = "".join(f'<div class="ret"><div class="lbl">{r["label"]}</div><div class="p {_cls(r["portfolio"])}">{_pct(r["portfolio"])}</div>'
                    f'<div class="b">SPY <span class="{_cls(r["benchmark"])}">{_pct(r["benchmark"])}</span></div></div>'
                    for r in model["money_returns"] if r["label"] != "1D")
    html_block('<div class="ret-grid">' + tiles + "</div>")
    rows = model["rows"]
    html_block('<div class="alloc-grid">' + donut_svg("By company", allocation(rows, "name"))
               + donut_svg("By country", allocation(rows, "country")) + donut_svg("By currency", allocation(rows, "currency")) + "</div>")
    lines = "".join(f'<tr><td>{esc(r["name"])} <span class="fl-tk">{esc(r["ticker"]) if not r.get("is_cash") else ""}</span></td>'
                    f'<td class="num">{r["weight"] * 100:.1f}%</td><td class="num">{_pct(r.get("gain_pct")) if not r.get("is_cash") else "–"}</td></tr>'
                    for r in rows)
    html_block(f'<div class="tbl-wrap"><table class="tbl"><tr><th>Holding</th><th class="num">Weight</th><th class="num">Gain</th></tr>{lines}</table></div>')
    st.caption("Shared from Verdant Filings. Returns are on the money invested; prices may be delayed.")
