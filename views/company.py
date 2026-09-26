from datetime import timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header, relative_time
from core.config import direct_fetch
from services import briefs, chat, events as events_svc, financials, github, insiders, personal, prices, watch
from services.classify import ORDER, categorize, label as cat_label
from services.pipeline import filings_for, get_company, search_companies, sync_company
from sources import Company, configured_sources, get_source

from .components import cached_english_news, cached_local_news, price_history, render_filing, render_news


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
    html_block(f'<div class="co-head">{logo_html(comp["name_en"], comp["ticker"], large=True)}'
               f'<h1 class="co-name">{esc(comp["name_en"])}</h1></div><div class="co-meta ko">{meta}{last}</div>')

    watching = watch.is_watching(user["id"], market, comp["ticker"])
    b1, b2, *_ = st.columns([1.1, 1.1, 3])
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
    chart = prices.chart_svg(hist, [r["filed_date"] for r in rows])
    if chart:
        html_block(chart)

    with st.expander("My notes on this company", icon=":material/edit_note:"):
        note = personal.get_note(user["id"], market, comp["ticker"])
        with st.form(f"note-{market}-{comp['ticker']}", border=False):
            body = st.text_area("Private notes", value=note, height=140, label_visibility="collapsed",
                                placeholder="Only you can see these notes.")
            if st.form_submit_button("Save notes"):
                personal.save_note(user["id"], market, comp["ticker"], body)
                st.toast("Notes saved")

    tab_filings, tab_fin, tab_ins, tab_news, tab_chat = st.tabs(["Filings", "Financials", "Insiders", "News", "Discussion"])

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
        stars = personal.starred(user["id"])
        for r in rows:
            render_filing(r, key="co", show_company=False, hist=hist, stars=stars)
        if src.attribution and rows:
            st.caption(src.attribution)

    with tab_news:
        local_label = f"{src.country} press, translated"
        options = ["English press"] + ([local_label] if src.news_local else [])
        press = st.segmented_control("Press", options, default="English press", key="co-press",
                                     label_visibility="collapsed") if len(options) > 1 else "English press"
        if press == local_label:
            with st.spinner("Translating headlines"):
                render_news(cached_local_news(market, comp["name_local"]))
        else:
            render_news(cached_english_news(comp["name_en"], comp["ticker"]))

    with tab_chat:
        discussion(market, comp["ticker"], user)


def _search_results(query: str) -> None:
    results = []
    for src in configured_sources():
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
            st.caption("Structured financial figures are available for Korean and US companies. For other markets, "
                       "see the annual and half-year reports in the Filings tab.")
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
    st.caption("Source: " + ("DART key accounts (consolidated where available)" if market == "KR" else "SEC company facts (XBRL)")
               + ". Figures as reported; periods are calendar-aligned.")


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
                         f'<td>{esc(r["role"])}</td><td>{esc(kind)}</td><td class="num">{(r["shares"] or 0):,.0f}</td>'
                         f'<td class="num">{price}</td><td class="num">{after}</td></tr>')
        body = "".join(lines)
        html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>Date</th><th>Who</th><th>Role</th><th>Type</th>'
                   f'<th class="num">Shares</th><th class="num">Price</th><th class="num">Holds after</th></tr>{body}</table></div>')
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
