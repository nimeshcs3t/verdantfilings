from datetime import timedelta

import streamlit as st

import re

from core.ui import esc, html_block, page_header
from services import ask, personal, portfolio as portfolio_svc, watch
from services.classify import ORDER, label
from services.search import search, to_csv
from sources import configured_sources, get_source, visible_sources

from .components import histories, render_filing

PERIODS = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "Last year": 365, "Everything": None}


def page() -> None:
    user = st.session_state["user"]
    page_header("Search", "Ask a question across your filings, or search every stored filing.")
    ask_box(user)
    c1, c2, c3 = st.columns([2.2, 1, 1], vertical_alignment="bottom")
    text = c1.text_input("Words", placeholder="e.g. buyback, rights offering, Samsung", key="s-text")
    markets = {s.country: s.market for s in visible_sources(user)}
    country = c2.selectbox("Country", ["All countries"] + list(markets), key="s-country")
    period = c3.selectbox("Period", list(PERIODS), index=2, key="s-period")
    c4, c5 = st.columns([3, 1], vertical_alignment="center")
    cats = c4.pills("Categories", [label(k) for k in ORDER], selection_mode="multi", key="s-cats",
                    label_visibility="collapsed")
    only_starred = c5.toggle("Starred only", key="s-starred")
    if len(text.strip()) >= 2:
        everywhere(user, text.strip())

    market = markets.get(country)
    days = PERIODS[period]
    since = (get_source(market).today() if market else max(s.today() for s in configured_sources())) - timedelta(days=days) \
        if days else None
    stars = personal.starred(user["id"])
    rows = search(text, market, since, stars if only_starred else None)
    if cats:
        rows = [r for r in rows if label(r["category"]) in cats]

    left, right = st.columns([3, 1], vertical_alignment="center")
    left.caption(f"{len(rows)} filing{'s' if len(rows) != 1 else ''}" + (" (showing the latest 300)" if len(rows) >= 300 else ""))
    if rows:
        right.download_button("Download CSV", to_csv(rows), file_name="filings.csv", mime="text/csv", width="stretch",
                              icon=":material/download:")
    else:
        html_block('<div class="empty">No filings match. Try fewer words or a longer period.</div>')
    several = len({r["market"] for r in rows}) > 1
    hist = histories([(r["market"], r["ticker"]) for r in rows[:60]])
    for r in rows[:150]:
        render_filing(r, key="s", country=get_source(r["market"]).country if several and get_source(r["market"]) else "",
                      hist=hist.get((r["market"], r["ticker"])), stars=stars)
    if len(rows) > 150:
        st.caption("Showing the first 150 here. The CSV has them all.")


ASK_PERIODS = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "Last year": 365}


def ask_box(user: dict) -> None:
    with st.container(border=True):
        st.markdown("**Ask your filings**")
        if not ask.available():
            st.caption("Add a Gemini key (GEMINI_API_KEY) in the app secrets to ask questions across filings.")
            return
        with st.form("ask-many", border=False):
            question = st.text_input("Question", placeholder="e.g. What did my companies announce about buybacks this month?",
                                     label_visibility="collapsed")
            c1, c2, c3 = st.columns([1, 1.3, 0.6], vertical_alignment="bottom")
            period = c1.selectbox("Period", list(ASK_PERIODS), index=1)
            scope = c2.selectbox("Companies", ["My watchlist and holdings", "All companies tracked here"])
            asked = c3.form_submit_button("Ask", type="primary", width="stretch")
        if asked and question.strip():
            pairs = None
            if scope.startswith("My"):
                pairs = {(w["market"], w["ticker"]) for w in watch.get_watchlist(user["id"])}
                pairs |= {(h["market"], h["ticker"]) for h in portfolio_svc.current_holdings(user["id"])}
            since = max(s.today() for s in configured_sources()) - timedelta(days=ASK_PERIODS[period])
            rows = [r for r in search("", None, since, limit=2000)
                    if pairs is None or (r["market"], r["ticker"]) in pairs]
            visible = {s.market for s in visible_sources(user)}
            rows = [r for r in rows if r["market"] in visible]
            with st.spinner("Reading the filings"):
                st.session_state["ask-result"] = ask.ask_filings(question, rows)
        result = st.session_state.get("ask-result")
        if result:
            answer, used = result

            def link(m):
                i = int(m.group(1))
                if 1 <= i <= len(used):
                    return f'<a href="{esc(used[i - 1]["url"])}" target="_blank" rel="noopener noreferrer">[{i}]</a>'
                return m.group(0)

            body = re.sub(r"\[(\d{1,3})\]", link, esc(answer)).replace("\n", "<br>")
            html_block(f'<div class="answer">{body}</div>')
            cited = sorted({int(n) for n in re.findall(r"\[(\d{1,3})\]", answer) if 1 <= int(n) <= len(used)})
            if cited:
                st.caption("Sources")
                html_block("".join(
                    f'<div class="ev-row"><b>[{i}]</b> {used[i - 1]["filed_date"]:%d %b} {esc(used[i - 1]["company_name"])}: '
                    f'<a href="{esc(used[i - 1]["url"])}" target="_blank" rel="noopener noreferrer">'
                    f'{esc(used[i - 1]["title_en"])}</a></div>' for i in cited))
            st.caption("AI answer from filing titles and overviews. Check the sources before relying on it.")



@st.cache_data(ttl=600, show_spinner=False)
def _company_hits(q: str, markets: tuple) -> list[tuple[str, str, str, str]]:
    from services.pipeline import search_companies
    from sources import get_source
    out = []
    for m in markets:
        src = get_source(m)
        if src is None or src.resolve_in_app:      # skip live lookups here; they're on the Watchlist page
            continue
        try:
            out += [(c.market, c.ticker, c.name_en, src.country) for c in search_companies(m, q)[:3]]
        except Exception:
            continue
    return out[:12]


def everywhere(user: dict, q: str) -> None:
    """Companies, IPOs, journal entries and notes matching the words, above the filings."""
    from core import nav
    from core.db import get_engine, notes
    from sqlalchemy import select
    from services import ipos as ipo_svc, journal
    low = q.lower()
    companies = _company_hits(q, tuple(sorted(s.market for s in visible_sources(user))))
    ipo_rows = [r for r in ipo_svc.listing() if low in f'{r["name"]} {r.get("ticker") or ""} {r.get("name_local") or ""} {r.get("sector") or ""}'.lower()][:6]
    entries = [e for e in journal.entries(user["id"]) if low in f"{e['title']} {e['body']} {e['tags']} {e['ticker'] or ''}".lower()][:6]
    with get_engine().connect() as conn:
        mine = [dict(r) for r in conn.execute(select(notes).where(notes.c.user_id == user["id"])).mappings()
                if low in (r["body"] or "").lower() or low == (r["ticker"] or "").lower()][:6]
    if not (companies or ipo_rows or entries or mine):
        return
    with st.container(border=True):
        if companies:
            st.markdown("**Companies**")
            cols = st.columns(3)
            for i, (m, t, name, country) in enumerate(companies):
                if cols[i % 3].button(f"{name} ({t}, {country})", key=f"ev-co-{m}-{t}", type="tertiary"):
                    nav.open_company(m, t)
        if ipo_rows:
            st.markdown("**IPOs**")
            html_block("".join(f'<div class="ev-row"><b>{(r["listing_date"].strftime("%d %b") if r.get("listing_date") else "TBA")}</b>'
                               f'{esc(r["name"])} <span>{esc(r.get("country") or "")}, {esc(r.get("sector") or "")}</span></div>' for r in ipo_rows))
        if entries:
            st.markdown("**Your journal**")
            html_block("".join(f'<div class="ev-row"><b>{e["entry_date"]:%d %b %Y}</b>{esc(e["ticker"] or "General")}: '
                               f'{esc(e["title"] or (e["body"] or "")[:80])}</div>' for e in entries))
        if mine:
            st.markdown("**Your company notes**")
            html_block("".join(f'<div class="ev-row"><b>{esc(n["ticker"])}</b>{esc((n["body"] or "")[:140])}</div>' for n in mine))
