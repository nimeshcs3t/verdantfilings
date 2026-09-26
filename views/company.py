from datetime import timedelta

import streamlit as st

from core.ui import esc, html_block, page_header, relative_time
from core.config import direct_fetch
from services import chat, github, watch
from services.pipeline import filings_for, get_company, search_companies, sync_company
from sources import Company, configured_sources, get_source

from .components import cached_english_news, cached_local_news, render_filing, render_news


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

    local = comp["name_local"] if comp["name_local"] != comp["name_en"] else ""
    meta = "".join(f"<span>{esc(x)}</span>" for x in (local, comp["ticker"], f"{src.country}, {src.regulator}") if x)
    html_block(f'<h1 class="co-name">{esc(comp["name_en"])}</h1><div class="co-meta ko">{meta}</div>')

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

    tab_filings, tab_news, tab_chat = st.tabs(["Filings", "News", "Discussion"])

    with tab_filings:
        rows = filings_for([(market, comp["ticker"])], src.today() - timedelta(days=90), limit=200)
        text = st.text_input("Filter", placeholder="Filter by title, e.g. dividend", label_visibility="collapsed",
                             key="co-filter")
        if text.strip():
            q = text.strip().lower()
            rows = [r for r in rows if q in (r["title_en"] or "").lower() or q in (r["title_local"] or "").lower()]
        if not comp.get("last_synced") and not direct_fetch():
            st.caption("This company's filings are being loaded for the first time. They'll appear within a few minutes.")
        else:
            st.caption(f"{len(rows)} filings in the last 90 days")
        for r in rows:
            render_filing(r, key="co", show_company=False)
        if src.attribution and rows:
            st.caption(src.attribution)

    with tab_news:
        press = st.segmented_control("Press", ["English press", "Korean press, translated"],
                                     default="English press", key="co-press", label_visibility="collapsed")
        if press == "Korean press, translated":
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
