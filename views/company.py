from datetime import timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header, relative_time
from core.config import direct_fetch
from services import valuation, briefs, chat, events as events_svc, fairvalue, financials, github, insiders, journal as journal_svc, personal, prices, watch
from services.classify import ORDER, categorize, label as cat_label
from services.pipeline import filings_for, get_company, search_companies, sync_company
from sources import Company, configured_sources, get_source, visible_sources

from .components import (cached_english_news, cached_local_news, cached_substack, price_history, render_filing,
                         render_news, render_substack)


def _selection(wl: list[dict]):
    sel = st.session_state.get("company_sel")
    qp = st.query_params
    if sel is None and qp.get("t"):
        sel = (qp.get("m", "KR"), qp.get("t"))
    if sel is None and wl:
        sel = (wl[0]["market"], wl[0]["ticker"])
    return sel


def page() -> None:
    user = st.session_state["user"]
    wl = watch.get_watchlist(user["id"])
    sel = _selection(wl)

    top_left, top_right = st.columns([1.2, 1], vertical_alignment="bottom")
    with top_left:
        options = [(w["market"], w["ticker"]) for w in wl]
        several = len({w["market"] for w in wl}) > 1
        labels = {(w["market"], w["ticker"]): f'{w["name_en"]}  ({w["ticker"]})'
                  + (f'  {get_source(w["market"]).country}' if several else "") for w in wl}
        if sel and sel not in options:
            options.insert(0, sel)
        if options:
            st.session_state["co-pick"] = sel if sel in options else options[0]
            st.selectbox("Your companies", options, format_func=lambda o: labels.get(o, o[1]),
                         key="co-pick", on_change=_on_pick)
    with top_right:
        query = st.text_input("Look up another company", placeholder="Name or ticker", key="co-search")
    if query.strip():
        _search_results(query)

    if not sel:
        page_header("Companies", "Look up a company by name or ticker to see its filings, news and discussion.")
        return

    market, ticker = sel
    if market in ("US", "OT"):                # a linked US / OTC line: show the home listing's filings instead
        from services.home_listing import home_of, manual
        if manual(ticker):
            try:
                home = home_of(market, ticker)
            except Exception:
                home = None
            if home:
                st.caption(f"{ticker} is linked to {home[2]} ({home[1]}, {get_source(home[0]).country}); "
                           "showing the home listing's filings.")
                if watch.is_watching(user["id"], market, ticker):      # follow the home listing from now on
                    watch.replace(user, (market, ticker), (home[0], home[1]))
                market, ticker = home[0], home[1]
                st.session_state["company_sel"] = (market, ticker)
            else:
                h_market, h_ticker = manual(ticker)
                h_src = get_source(h_market)
                why = ("that market isn't switched on in the app's secrets" if not (h_src and h_src.is_configured())
                       else f"{h_ticker} couldn't be found on {h_src.regulator} just now")
                st.warning(f"{ticker} is linked to {h_ticker} ({h_market}), but {why}. Showing the US listing for now.")
    src = get_source(market)
    with st.spinner("Loading company"):
        try:
            comp = get_company(market, ticker)
        except Exception:
            comp = None
    if comp is None:
        st.error(f"Couldn't find a listed company for {ticker}. Check the ticker, or try searching by name.")
        return
    st.query_params.update({"m": market, "t": comp["ticker"]})

    if direct_fetch():
        try:
            with st.spinner("Checking for new filings"):
                sync_company(market, comp["ticker"])
        except Exception:
            st.caption("The regulator didn't respond. Showing saved filings.")
    elif not comp.get("last_synced"):
        github.trigger_sync()

    hist = price_history(market, comp["ticker"])
    local = comp["name_local"] if comp["name_local"] != comp["name_en"] else ""
    meta = "".join(f"<span>{esc(x)}</span>" for x in (local, comp["ticker"], f"{src.country}, {src.regulator}") if x)
    last = (f'<span>{hist[-1][1]:,.2f} {prices.move_html(prices.last_move(hist))}</span>' if hist else "")
    html_block(f'<div class="co-head">{logo_html(comp["name_en"], comp["ticker"], large=True, market=market)}'
               f'<h1 class="co-name">{esc(comp["name_en"])}</h1></div><div class="co-meta ko">{meta}{last}</div>')

    watching = watch.is_watching(user["id"], market, comp["ticker"])
    b1, b2, b3, b4, _ = st.columns([1.1, 1.1, 1, 1.3, 0.7])
    if watching:
        if b1.button("Remove from watchlist", width="stretch"):
            watch.remove(user["id"], market, comp["ticker"])
            st.rerun()
    else:
        if b1.button("Add to watchlist", type="primary", width="stretch"):
            _, err = watch.add(user, market, comp["ticker"])
            if err:
                st.warning(err)
            else:
                st.rerun()
    company_obj = Company(market, comp["ticker"], comp["source_id"], comp["name_local"], comp["name_en"])
    for label, url in src.external_links(company_obj)[:1]:
        b2.link_button(label, url, width="stretch")
    from urllib.parse import quote_plus
    from services.logos import website
    site = website(market, comp["ticker"])
    if site:
        b3.link_button("Website", site, width="stretch", icon=":material/language:")
    else:
        b3.link_button("Find website", "https://www.google.com/search?q=" + quote_plus(f'{comp["name_en"]} official website'),
                       width="stretch", icon=":material/travel_explore:")

    if market == "OT":                       # foreign shares traded OTC: their filings are in the home market
        from services.home_listing import find as home_listing
        try:
            home = home_listing(comp["ticker"], comp["name_en"])
        except Exception:
            home = None
        if home:
            h_market, h_ticker, h_name = home
            h1, h2 = st.columns([3, 1], vertical_alignment="center")
            h1.caption(f"{comp['ticker']} is the US over-the-counter line of {h_name}, which files in "
                       f"{get_source(h_market).country}. Its official filings are on the home listing.")
            h2.button(f"Open {h_ticker} ({get_source(h_market).country})", key="co-home", width="stretch",
                      on_click=_pick, args=(h_market, h_ticker))
    from services import groups as groups_svc
    from core.ui import groups_html
    from .components import group_picker
    assigned = groups_svc.assignments(user["id"]).get((market, comp["ticker"]))
    with b4.popover("Sector & themes", icon=":material/category:", width="stretch"):
        group_picker(user["id"], market, comp["ticker"], key="co")
    if assigned:
        html_block(f'<div style="margin:-.3rem 0 .6rem">{groups_html(assigned)}</div>')

    rows = filings_for([(market, comp["ticker"])], src.today() - timedelta(days=180), limit=300)
    for r in rows:
        r["category"] = categorize(r["title_en"], r["title_local"], r.get("price_sensitive"))
    brief, brief_when = briefs.get(market, comp["ticker"])
    if brief:
        html_block(f'<div class="brief-box">{esc(brief)}<div class="meta">AI summary of recent filings, '
                   f'updated {esc(relative_time(brief_when))}. Check the filings before relying on it.</div></div>')
    coming = events_svc.upcoming([(market, comp["ticker"])], days=120)
    if coming:
        html_block('<div class="events">' + "".join(f'<span class="event"><b>{e["event_date"]:%d %b}</b>{esc(e["label"])}</span>'
                                                    for e in coming[:6]) + "</div>")
    from services.portfolio import currency_for
    q = valuation.quick_view(market, comp["ticker"], hist, currency_for(market, comp["ticker"]))
    tiles = [(label, valuation.fmt(q, key, kind)) for label, key, kind in valuation.ROWS if key != "price"]
    tiles = [t for t in tiles if t[1] != "–"]
    if tiles:
        html_block('<div class="qv">' + "".join(f'<div class="stat"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div></div>'
                                                for k, v in tiles) + "</div>")
        if q.get("fin_period"):
            st.caption(f"Financial figures for {q['fin_period']}; valuation at the last price. Not investment advice.")
    levels = [(v, label, "target") for v, label in journal_svc.targets(user["id"], market, comp["ticker"])]
    fv_row = fairvalue.get_all(user["id"]).get((market, comp["ticker"]))
    if fv_row:
        levels.append((fv_row["value"], "Fair value", "fair"))
    chart = prices.chart_svg(hist, [r["filed_date"] for r in rows], days=250, levels=levels)
    if chart:
        html_block(chart)

    fv = fairvalue.get_all(user["id"]).get((market, comp["ticker"]))
    last_price = hist[-1][1] if hist else None
    label = "My fair value"
    if fv and last_price:
        up = fairvalue.upside(last_price, fv["value"])
        label = f"My fair value: {fv['value']:,.2f} ({up * 100:+.1f}% from {last_price:,.2f})"
    with st.expander(label, icon=":material/target:"):
        with st.form(f"fv-{market}-{comp['ticker']}", border=False):
            c1, c2 = st.columns(2)
            value = c1.number_input("Fair value per share", min_value=0.0, value=float(fv["value"]) if fv else 0.0, step=1.0,
                                    help="Your own estimate, in the share's trading currency. 0 removes it.")
            pct = c2.number_input("Alert when within %", min_value=0.0, max_value=50.0, value=float(fv["alert_pct"]) if fv else 10.0, step=1.0)
            note = st.text_area("How you got there", value=(fv or {}).get("note") or "", height=80)
            if st.form_submit_button("Save fair value"):
                fairvalue.save(user["id"], market, comp["ticker"], value, pct, note)
                st.rerun()

    entries = journal_svc.entries(user["id"], market, comp["ticker"])
    with st.expander(f"Journal ({len(entries)})", icon=":material/history_edu:"):
        from views.journal import entry_card, entry_form
        entry_form(user["id"], preset=(market, comp["ticker"]), key=f"cj-{comp['ticker']}")
        for e in entries[:20]:
            entry_card(e, show_company=False, key="cj")

    with st.expander("My notes on this company", icon=":material/edit_note:"):
        note = personal.get_note(user["id"], market, comp["ticker"])
        with st.form(f"note-{market}-{comp['ticker']}", border=False):
            body = st.text_area("Private notes", value=note, height=140, label_visibility="collapsed",
                                placeholder="Only you can see these notes.")
            if st.form_submit_button("Save notes"):
                personal.save_note(user["id"], market, comp["ticker"], body)
                st.toast("Notes saved")

    tab_filings, tab_fin, tab_ins, tab_news, tab_peers, tab_chat = st.tabs(["Filings", "Financials", "Insiders", "News",
                                                                            "Peers", "Discussion"])

    with tab_peers:
        peers_tab(user, market, comp)

    with tab_fin:
        financials_tab(market, comp["ticker"])

    with tab_ins:
        insiders_tab(market, comp["ticker"], rows)

    with tab_filings:
        present = [k for k in ORDER if any(r["category"] == k for r in rows)]
        c1, c2 = st.columns([1, 1.4], vertical_alignment="center")
        text = c1.text_input("Filter", placeholder="Filter by title, e.g. dividend", label_visibility="collapsed",
                             key="co-filter")
        picked = c2.pills("Categories", [cat_label(k) for k in present], selection_mode="multi",
                          key="co-cats", label_visibility="collapsed") if len(present) > 1 else []
        if text.strip():
            q = text.strip().lower()
            rows = [r for r in rows if q in (r["title_en"] or "").lower() or q in (r["title_local"] or "").lower()]
        if picked:
            rows = [r for r in rows if cat_label(r["category"]) in picked]
        if not comp.get("last_synced") and not direct_fetch():
            st.caption("This company's filings are being loaded for the first time. They'll appear within a few minutes.")
        else:
            st.caption(f"{len(rows)} filings in the last 180 days")
        stars = personal.flags(user["id"])
        for r in rows:
            render_filing(r, key="co", show_company=False, hist=hist, stars=stars)
        if src.attribution and rows:
            st.caption(src.attribution)

    with tab_news:
        local_label = f"{src.country} press, translated"
        options = ["English press"] + ([local_label] if src.news_local else []) + ["Substack"]
        press = st.segmented_control("Press", options, default="English press", key="co-press",
                                     label_visibility="collapsed") or "English press"
        if press == local_label:
            with st.spinner("Translating headlines"):
                render_news(cached_local_news(market, comp["name_local"]))
        elif press == "Substack":
            from services import settings as settings_svc
            follows = tuple(settings_svc.get(user["id"]).get("substack_feeds") or [])
            with st.spinner("Looking for Substack posts"):
                render_substack(cached_substack(comp["name_en"], comp["ticker"], follows))
        else:
            render_news(cached_english_news(comp["name_en"], comp["ticker"]))

    with tab_chat:
        discussion(market, comp["ticker"], user)


def _search_results(query: str) -> None:
    results = []
    for src in visible_sources(st.session_state.get("user")):
        try:
            results += search_companies(src.market, query)
        except Exception:
            st.caption(f"{src.regulator} search is unavailable right now.")
    if not results:
        st.caption("No matches.")
    for c in results[:8]:
        label = f"{c.name_en}  ({c.ticker})" + (f"  {c.name_local}" if c.name_local != c.name_en else "")
        st.button(label, key=f"sr-{c.market}-{c.ticker}", type="tertiary",
                  on_click=_pick, args=(c.market, c.ticker))


def _on_pick() -> None:
    st.session_state["company_sel"] = st.session_state["co-pick"]


def _pick(market: str, ticker: str) -> None:
    st.session_state["company_sel"] = (market, ticker)
    st.session_state["co-search"] = ""


@st.fragment(run_every=timedelta(seconds=20))
def discussion(market: str, ticker: str, user: dict) -> None:
    with st.form(f"post-{market}-{ticker}", clear_on_submit=True, border=False):
        body = st.text_area("Add to the discussion", max_chars=chat.MAX_LEN, height=90,
                            placeholder="Share what you think about this company's latest filings")
        if st.form_submit_button("Post", type="primary"):
            err = chat.post(user, market, ticker, body)
            if err:
                st.warning(err)
            else:
                st.rerun(scope="fragment")
    msgs = chat.recent(market, ticker)
    if not msgs:
        html_block('<div class="empty">No messages yet. Start the conversation.</div>')
    for m in msgs:
        html_block(f'<div class="msg"><span class="msg-who">{esc(m["username"])}</span>'
                   f'<span class="msg-when">{esc(relative_time(m["created_at"]))}</span>'
                   f'<div class="msg-body">{esc(m["body"])}</div></div>')
        if user["role"] == "admin" or m["user_id"] == user["id"]:
            if st.button("Delete", key=f"del-{m['id']}", type="tertiary"):
                chat.remove(m["id"], user)
                st.rerun(scope="fragment")


def financials_tab(market: str, ticker: str) -> None:
    data = financials.get(market, ticker)
    if not data["annual"] and not data["quarter"]:
        if market in financials.SUPPORTED:
            st.caption("Financial figures load within a day of adding a company to a watchlist.")
        else:
            st.caption("Structured financial figures are available for Korea, the USA, Taiwan, France, the UK, Norway, "
                       "Sweden, Denmark and Finland. For other markets, see the annual and half-year reports in the Filings tab.")
        return
    kinds = [k for k in ("annual", "quarter") if data[k]]
    kind = st.segmented_control("Period", kinds, default=kinds[0], key="fin-kind", label_visibility="collapsed",
                                format_func=lambda k: "Yearly" if k == "annual" else "Quarterly") if len(kinds) > 1 else kinds[0]
    rows = data[kind or kinds[0]]
    html_block('<div class="fin-grid">' + financials.bars_svg(rows, "revenue", "Revenue")
               + financials.bars_svg(rows, "op_income", "Operating profit")
               + financials.bars_svg(rows, "net_income", "Net profit") + "</div>")
    cur = rows[0]["currency"]
    body = "".join(f'<tr><td>{esc(r["period"])}</td><td class="num">{financials.money(r["revenue"], cur)}</td>'
                   f'<td class="num">{financials.money(r["op_income"], cur)}</td>'
                   f'<td class="num">{financials.money(r["net_income"], cur)}</td>'
                   f'<td class="num">{(r["op_income"] / r["revenue"] * 100):.1f}%</td></tr>'
                   if r.get("revenue") and r.get("op_income") is not None else
                   f'<tr><td>{esc(r["period"])}</td><td class="num">{financials.money(r["revenue"], cur)}</td>'
                   f'<td class="num">{financials.money(r["op_income"], cur)}</td>'
                   f'<td class="num">{financials.money(r["net_income"], cur)}</td><td class="num">–</td></tr>'
                   for r in reversed(rows))
    html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>Period</th><th class="num">Revenue</th>'
               '<th class="num">Operating profit</th><th class="num">Net profit</th><th class="num">Op. margin</th></tr>'
               f'{body}</table></div>')
    source = {"KR": "DART key accounts (consolidated where available)", "US": "SEC company facts (XBRL)",
              "TW": "TWSE open data. Quarters are cumulative for the year to date; history builds up from each new quarter"}.get(
        market, "the company's digital annual reports (ESEF) via filings.xbrl.org; yearly figures")
    st.caption(f"Source: {source}. Figures as reported.")


def insiders_tab(market: str, ticker: str, filings_rows: list[dict]) -> None:
    rows = insiders.recent(market, ticker)
    if rows:
        s = insiders.summary(rows)
        cur = "USD" if market == "US" else ""
        stats = [("Shares bought", f"{s['bought']:,.0f}"), ("Shares sold", f"{s['sold']:,.0f}"),
                 ("Buyers", s["buyers"]), ("Sellers", s["sellers"])]
        if s["value_bought"] or s["value_sold"]:
            stats += [("Value bought", financials.money(s["value_bought"], cur)), ("Value sold", financials.money(s["value_sold"], cur))]
        html_block('<div class="stat-grid">' + "".join(f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div></div>'
                                                       for k, v in stats) + "</div>")
        lines = []
        for r in rows[:60]:
            if not r.get("tx_date"):
                continue
            price = f'{r["price"]:,.2f}' if r.get("price") else ""
            after = f'{r["after"]:,.0f}' if r.get("after") is not None else ""
            kind = insiders.CODE_LABEL.get(r["code"], r["code"])
            lines.append(f'<tr><td>{r["tx_date"]:%d %b %Y}</td><td class="ko">{esc(r["person"])}</td>'
                         f'<td class="opt">{esc(r["role"])}</td><td>{esc(kind)}</td><td class="num">{(r["shares"] or 0):,.0f}</td>'
                         f'<td class="num">{price}</td><td class="num opt">{after}</td></tr>')
        body = "".join(lines)
        html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>Date</th><th>Who</th><th class="opt">Role</th><th>Type</th>'
                   f'<th class="num">Shares</th><th class="num">Price</th><th class="num opt">Holds after</th></tr>{body}</table></div>')
        st.caption("Last 180 days. " + ("From Form 4 filings." if market == "US" else "From DART executive and major shareholder reports."))
        return
    insider_filings = [r for r in filings_rows if r.get("category") in ("insider", "ownership")]
    if insider_filings:
        st.caption("Insider and ownership filings in the last 180 days:")
        for r in insider_filings[:20]:
            render_filing(r, key="ins", show_company=False)
    elif market in ("US", "KR"):
        st.caption("No insider trades found yet. They load within a day of adding a company to a watchlist.")
    else:
        st.caption("No insider or ownership filings in the last 180 days.")


def peers_tab(user: dict, market: str, comp: dict) -> None:
    """Peers side by side, their latest filings, and filings that link this company with others."""
    import pandas as pd
    from services import connections
    ticker = comp["ticker"]
    my_peers = connections.peers(user["id"], market, ticker)
    with st.spinner("Comparing"):
        rows = connections.peer_table(user["id"], market, ticker)
    pct = lambda v: None if v is None else round(v * 100, 1)
    if my_peers:
        df = pd.DataFrame([{"Company": ("▸ " if r["self"] else "") + r["name"], "Ticker": r["ticker"], "Country": r["country"],
                            "Price": r["price"], "Currency": r["currency"], "1-year %": pct(r["ret1y"]),
                            "P/E": r["pe"], "P/S": r["ps"], "Growth %": pct(r["growth"]),
                            "Op. margin %": pct(r["op_margin"]), "Net margin %": pct(r["net_margin"])} for r in rows])
        for col in ("Price", "1-year %", "P/E", "P/S", "Growth %", "Op. margin %", "Net margin %"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        st.dataframe(df, hide_index=True, width="stretch", column_config={
            "Price": st.column_config.NumberColumn(format="%.2f"),
            "P/E": st.column_config.NumberColumn(format="%.1f"), "P/S": st.column_config.NumberColumn(format="%.1f"),
            **{c: st.column_config.NumberColumn(format="%+.1f") for c in ("1-year %", "Growth %", "Op. margin %", "Net margin %")}})
        st.caption("Each in its own currency, latest reported year. Peers' filings are collected like any company you look up.")
    else:
        st.caption("No peers yet. Add competitors, suppliers or customers to compare them and see their filings here.")

    c1, c2 = st.columns([2, 1])
    with c1:
        q = st.text_input("Add a peer", placeholder="Name or ticker, any country", key=f"peer-q-{ticker}")
        if q.strip() and len(q.strip()) >= 2:
            from views.search import _company_hits
            from sources import visible_sources
            hits = _company_hits(q.strip(), tuple(sorted(s.market for s in visible_sources(user))))
            for m, t, name, country in hits[:8]:
                if st.button(f"{name} ({t}, {country})", key=f"peer-add-{m}-{t}", type="tertiary", icon=":material/add:"):
                    err = connections.add_peer(user["id"], market, ticker, m, t)
                    st.warning(err) if err else st.rerun()
            if not hits:
                st.caption("No match among listed companies.")
    with c2:
        sugg = [s for s in connections.suggestions(market, ticker) if ("US", s["ticker"]) not in my_peers]
        if sugg:
            st.caption("Suggested (same industry, similar size)")
            for s_ in sugg:
                if st.button(f"{s_['name']} ({s_['ticker']})", key=f"peer-sug-{s_['ticker']}", type="tertiary", icon=":material/add:"):
                    err = connections.add_peer(user["id"], market, ticker, "US", s_["ticker"])
                    st.warning(err) if err else st.rerun()
    if my_peers:
        with st.expander("Remove peers"):
            for m, t in my_peers:
                if st.button(f"Remove {t}", key=f"peer-rm-{m}-{t}", type="tertiary"):
                    connections.remove_peer(user["id"], market, ticker, m, t)
                    st.rerun()
        st.subheader("Peers' latest filings", divider=False)
        pf = connections.peer_filings(user["id"], market, ticker)
        flags = personal.flags(user["id"])
        for f in pf:
            render_filing(f, key="peerf", stars=flags)
        if not pf:
            st.caption("No peer filings stored yet; they arrive with the next background run.")

    named_in = connections.mentions_of(market, ticker)
    named_market = connections.market_mentions_of(market, ticker)
    names_others = connections.mentioned_by(market, ticker)
    if named_in or names_others or named_market:
        st.subheader("Connections in filings", divider=False)
    if named_in:
        st.caption(f"Other companies' filings that name {comp['name_en']} (contracts, supply, deals)")
        flags = personal.flags(user["id"])
        for f in named_in[:15]:
            render_filing(f, key="named", stars=flags)
    if named_market:
        st.caption(f"Announcements from other companies on the market that name {comp['name_en']} (last 30 days)")
        from sources import get_source
        html_block("".join(
            f'<div class="ev-row"><b>{r["filed_date"]:%d %b}</b>{esc(r["company"] or r["ticker"] or "")}: '
            f'{esc(r["title_en"] or r["title_local"])} <span>{esc((get_source(r["market"]) or type("x", (), {"country": r["market"]})).country)} · '
            f'<a href="{esc(r["url"])}" target="_blank" rel="noopener noreferrer">Original</a></span></div>' for r in named_market[:15]))
    if names_others:
        st.caption(f"{comp['name_en']}'s filings that name other companies you follow")
        html_block("".join(f'<div class="ev-row"><b>{r["filed_date"]:%d %b}</b>{esc(r["title_en"])} '
                           f'<span>names {esc(r["m_name"] or r["m_ticker"])}</span></div>' for r in names_others[:15]))
