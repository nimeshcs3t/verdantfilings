from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import portfolio, prices, watch
from services.pipeline import filings_for, get_company, search_companies
from services.portfolio_calc import PERIODS, Series, allocation, daily_values, period_returns, positions, rebalance, twr_index
from sources import configured_sources, get_source, visible_sources

from .portfolio_charts import donut_svg, performance_svg

YEARS = 4
BENCHMARKS = {"S&P 500 (SPY)": "SPY", "Nasdaq 100 (QQQ)": "QQQ", "No benchmark": None}
CHART_PERIODS = ["1M", "3M", "6M", "YTD", "1Y", "2Y", "3Y", "All"]


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


def build(txs: list[dict], base: str, bench_symbol: str | None) -> dict:
    """Everything the page shows, in the base currency."""
    pairs = sorted({(t["market"], t["ticker"]) for t in txs})
    currency_of = {p: portfolio.currency_for(*p) for p in pairs}
    with ThreadPoolExecutor(max_workers=8) as pool:
        hist = dict(zip(pairs, pool.map(lambda p: long_history(*p), pairs)))
    currencies = (set(currency_of.values()) | ({base} if base != "USD" else set())) - {"USD"}
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
    daily = daily_values(usable, price_series, fxs, currency_of, base, days, base_fx)
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
    total = sum(r["value_base"] or 0 for r in rows)
    for r in rows:
        r["weight"] = (r["value_base"] or 0) / total if total else 0
        r["gain_base"] = (r["value_base"] - r["cost_base"]) if r["value_base"] is not None else None
        r["gain_pct"] = (r["value_base"] / r["cost_base"] - 1) if r["value_base"] is not None and r["cost_base"] else None
    realized = sum(p["realized_base"] for p in pos.values())
    cost = sum(r["cost_base"] for r in rows)
    today_change = (daily[-1]["value"] - daily[-2]["value"] - daily[-1]["flow"]) if len(daily) >= 2 else None
    rows.sort(key=lambda r: -(r["value_base"] or 0))
    return {"rows": rows, "daily": daily, "index": index, "bench": bench_base, "problems": problems,
            "returns": period_returns(index, bench_base) if index else [], "total": total, "cost": cost,
            "realized": realized, "today_change": today_change, "convert": convert}


def page() -> None:
    user = st.session_state["user"]
    uid = user["id"]
    page_header("Portfolio", "Returns, allocation and rebalancing for your holdings, with price alerts.")
    txs = portfolio.list_transactions(uid)
    home = portfolio.home_currency(uid)

    if "pf-hide" not in st.session_state:
        st.session_state["pf-hide"] = portfolio.hide_amounts(uid)
    c1, c2, c3, c4 = st.columns([1.2, 1, 1.3, 0.9], vertical_alignment="bottom")
    mode = c1.segmented_control("Show values in", ["USD", "Home currency"], default="USD", key="pf-mode") or "USD"
    hide = c4.toggle("Hide amounts", key="pf-hide", help="Hides money values and share counts; percentages stay visible.")
    if hide != portfolio.hide_amounts(uid):
        portfolio.set_hide_amounts(uid, hide)
    chosen = c2.selectbox("Home currency", portfolio.HOME_CURRENCIES,
                          index=portfolio.HOME_CURRENCIES.index(home) if home in portfolio.HOME_CURRENCIES else 0)
    if chosen != home:
        portfolio.set_home_currency(uid, chosen)
        home = chosen
    bench_label = c3.selectbox("Benchmark", list(BENCHMARKS), key="pf-bench")
    base = "USD" if mode == "USD" else home
    bench_symbol = BENCHMARKS[bench_label]

    t_over, t_hold, t_reb, t_tx, t_alert = st.tabs(["Overview", "Holdings", "Rebalance", "Transactions", "Price alerts"])
    model = None
    if txs:
        with st.spinner("Calculating returns"):
            model = build(txs, base, bench_symbol)
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
    with t_alert:
        alerts_tab(user)


def overview(model: dict, base: str, bench_symbol: str | None) -> None:
    period = st.segmented_control("Chart period", CHART_PERIODS, default="1Y", key="pf-period",
                                  label_visibility="collapsed") or "1Y"
    index = model["index"]
    end = index[-1][0]
    start = {"1M": end - timedelta(days=30), "3M": end - timedelta(days=91), "6M": end - timedelta(days=182),
             "YTD": date(end.year, 1, 1), "1Y": end - timedelta(days=365), "2Y": end - timedelta(days=730),
             "3Y": end - timedelta(days=1095), "All": index[0][0]}[period]
    part = [(d, v) for d, v in index if d >= start]
    if len(part) >= 2:
        bench = [(d, model["bench"].at(d)) for d, _ in part] if model["bench"] else []
        html_block(performance_svg(part, [(d, v) for d, v in bench if v], bench_symbol or ""))
    else:
        st.caption("Not enough history for this period yet.")

    tiles = []
    for r in model["returns"]:
        b = (f'<div class="b">{esc(bench_symbol)} <span class="{_cls(r["benchmark"])}">{_pct(r["benchmark"])}</span></div>'
             if bench_symbol and r["benchmark"] is not None else "")
        note = '<div class="note">since first buy</div>' if r["partial"] else ""
        tiles.append(f'<div class="ret"><div class="lbl">{r["label"]}</div>'
                     f'<div class="p {_cls(r["portfolio"])}">{_pct(r["portfolio"])}</div>{b}{note}</div>')
    html_block('<div class="ret-grid">' + "".join(tiles) + "</div>")
    st.caption("Time-weighted returns in " + base + ", so adding or withdrawing money doesn't distort them. "
               + (f"{bench_symbol} is price return in the same currency." if bench_symbol else ""))

    total, cost, realized = model["total"], model["cost"], model["realized"]
    gain = total - cost + realized
    stats = [("Value", _money(total, base), ""),
             ("Total gain", _money(gain, base, True), f'{_pct(gain / cost if cost else None)} on {_money(cost, base)} invested'),
             ("Today", _money(model["today_change"], base, True) if model["today_change"] is not None else "–",
              _pct(model["returns"][0]["portfolio"]) if model["returns"] else ""),
             ("Realised gains", _money(realized, base, True), "from sales")]
    html_block('<div class="stat-grid">' + "".join(
        f'<div class="stat"><div class="k">{k}</div><div class="v">{esc(v)}</div><div class="k">{esc(sub)}</div></div>'
        for k, v, sub in stats) + "</div>")

    rows = model["rows"]
    html_block('<div class="alloc-grid">' + donut_svg("By company", allocation(rows, "name"))
               + donut_svg("By country", allocation(rows, "country"))
               + donut_svg("By currency", allocation(rows, "currency")) + "</div>")


def holdings_table(model: dict, base: str, uid: int) -> None:
    lines = []
    for r in model["rows"]:
        src = get_source(r["market"])
        recent = len(filings_for([(r["market"], r["ticker"])], src.today() - timedelta(days=7))) if src else ""
        lines.append(
            f'<tr><td>{logo_html(r["name"], r["ticker"])}{esc(r["name"])} <span class="fl-tk">{esc(r["ticker"])}</span></td>'
            f'<td class="num">{_shares(r["shares"])}</td><td class="num">{_money(r["avg"], r["currency"], always=True)}</td>'
            f'<td class="num">{_money(r["last"], r["currency"], always=True)}</td><td class="num">{_money(r["value_base"], base)}</td>'
            f'<td class="num">{r["weight"] * 100:.1f}%</td>'
            f'<td class="num"><span class="mv {_cls(r["gain_base"])}">{_money(r["gain_base"], base, True)}'
            f' ({_pct(r["gain_pct"])})</span></td><td class="num">{prices.move_html(r["day"])}</td>'
            f'<td class="num">{recent or ""}</td></tr>')
    html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>Company</th><th class="num">Shares</th>'
               '<th class="num">Avg cost</th><th class="num">Last price</th><th class="num">Value</th>'
               '<th class="num">Weight</th><th class="num">Gain</th><th class="num">Today</th>'
               f'<th class="num">Filings 7d</th></tr>{"".join(lines)}</table></div>')
    st.caption(f"Cost and last price in each stock's own currency; value and gain in {base}, with cost converted at "
               "the exchange rate on each purchase date (so gains include currency moves). Prices may be delayed.")


def rebalance_tab(model: dict, base: str, uid: int) -> None:
    rows = model["rows"]
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
    cash = c1.number_input(f"New cash to invest ({base})", min_value=0.0, value=0.0, step=100.0)
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
                                help="In the stock's own currency, e.g. AUD for Australian shares.")
        fees = c7.number_input("Fees", min_value=0.0, step=1.0, format="%.2f")
        also = st.checkbox("Also follow its filings (add to watchlist; not available for Other)", value=True)
        if st.form_submit_button("Save", type="primary"):
            market = markets[country]
            comp = _resolve(market, query)
            if not comp and market != portfolio.OTHER:
                st.warning("Couldn't find that company. Use its ticker, e.g. 005930, AAPL or EOS.")
            else:
                err = portfolio.add_transaction(uid, market, comp["ticker"], kind.lower(), day, shares, price, fees)
                if err:
                    st.warning(err)
                else:
                    if also and kind == "Buy" and market != portfolio.OTHER and not watch.is_watching(uid, market, comp["ticker"]):
                        watch.add(user, market, comp["ticker"])
                    st.toast(f"Saved {kind.lower()} of {comp['name_en']}")
                    st.rerun()
    if not txs:
        st.caption("No transactions yet.")
        return
    st.caption("Holdings saved before transactions existed were turned into buys dated the day you added them. "
               "For accurate returns, delete those and add them again with the real purchase date.")
    names = {}
    for t in txs[:200]:
        key = (t["market"], t["ticker"])
        if key not in names:
            names[key] = ((portfolio.symbol_info(t["ticker"]) or {}).get("name") if t["market"] == portfolio.OTHER
                          else (get_company(*key, resolve=False) or {}).get("name_en")) or t["ticker"]
        cur = portfolio.currency_for(*key)
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        fees = (f', fees {MASK if _hidden() else f"{t['fees']:,.2f}"}' if t["fees"] else "")
        price = MASK if _hidden() else f'{t["price"]:,.4g}'
        c1.markdown(f'{t["tx_date"]:%d %b %Y}  **{t["kind"].capitalize()}** {_shares(t["shares"])} × '
                    f'{esc(names[key])} ({esc(t["ticker"])}) at {price} {cur}{fees}', unsafe_allow_html=True)
        if c2.button("Delete", key=f"tx-del-{t['id']}", type="tertiary"):
            portfolio.remove_transaction(uid, t["id"])
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
