from datetime import date, timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import journal, portfolio, watch
from services.pipeline import get_company

KIND_CHIP = {"thesis": "earnings", "buy": "buyback", "sell": "mna", "update": "periodic", "review": "meeting", "lesson": "ownership"}


def _companies(uid: int) -> dict[str, tuple[str | None, str | None]]:
    out = {"General (no company)": (None, None)}
    for w in watch.get_watchlist(uid):
        out[f"{w['name_en']} ({w['ticker']})"] = (w["market"], w["ticker"])
    for h in portfolio.current_holdings(uid):
        if (h["market"], h["ticker"]) not in out.values():
            name = (get_company(h["market"], h["ticker"], resolve=False) or {}).get("name_en", h["ticker"])
            out[f"{name} ({h['ticker']})"] = (h["market"], h["ticker"])
    return out


def entry_form(uid: int, preset: tuple[str, str] | None = None, key: str = "j") -> None:
    companies = _companies(uid)
    labels = list(companies)
    index = next((i for i, k in enumerate(labels) if companies[k] == preset), 0) if preset else 0
    with st.form(f"{key}-add", clear_on_submit=True, border=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        company = c1.selectbox("Company", labels, index=index, disabled=bool(preset))
        kind = c2.selectbox("Entry", list(journal.KINDS), format_func=lambda k: journal.KINDS[k])
        day = c3.date_input("Date", value=date.today(), max_value=date.today())
        title = st.text_input("Title", placeholder="e.g. Why I'm buying: margin recovery and buybacks")
        body = st.text_area("Notes", height=150, placeholder="Your reasoning, what would prove you wrong, price levels, "
                                                             "what to watch in the next results...")
        c4, c5, c6, c7 = st.columns([1.2, 1.2, 1, 1.1])
        conviction = c4.slider("Conviction", 1, 5, 3)
        tags = c5.text_input("Tags", placeholder="e.g. dividend, turnaround")
        target = c6.number_input("Price target", min_value=0.0, value=0.0, step=1.0,
                                 help="Optional, in the share's trading currency. Drawn on the company's price chart.")
        review = c7.date_input("Review on (optional)", value=None, min_value=date.today())
        if st.form_submit_button("Save entry", type="primary"):
            market, ticker = companies[company]
            err = journal.add(uid, market, ticker, day, kind, title, body, conviction, tags, review, target or None)
            st.warning(err) if err else (st.toast("Saved to your journal"), st.rerun())


def entry_card(e: dict, show_company: bool = True, key: str = "j") -> None:
    uid = st.session_state["user"]["id"]
    name = ""
    if show_company and e["ticker"]:
        comp = get_company(e["market"], e["ticker"], resolve=False) or {}
        name = (f'<div class="fl-co">{logo_html(comp.get("name_en", e["ticker"]), e["ticker"], market=e["market"])}'
                f'{esc(comp.get("name_en", e["ticker"]))}<span class="fl-tk">{esc(e["ticker"])}</span></div>')
    stars = "●" * (e["conviction"] or 0) + "○" * (5 - (e["conviction"] or 0)) if e["conviction"] else ""
    if e.get("target_price"):
        stars += f"  Target {e['target_price']:,.2f}"
    tags = "".join(f'<span class="chip c-other">{esc(t.strip())}</span>' for t in (e["tags"] or "").split(",") if t.strip())
    review = ""
    if e["review_on"]:
        due = e["review_on"] <= date.today()
        review = f'<span class="fl-flag">{"Review due" if due else "Review"} {e["review_on"]:%d %b %Y}</span>'
    html_block(f'<div class="fl-row" style="grid-template-columns:86px 1fr"><div class="fl-date">{e["entry_date"]:%d %b %Y}</div>'
               f'<div>{name}<div class="fl-title"><span class="chip c-{KIND_CHIP.get(e["kind"], "other")}" style="margin:0 8px 0 0">'
               f'{esc(journal.KINDS[e["kind"]])}</span>{esc(e["title"] or "")}{review}</div>'
               f'<div class="fl-orig">{esc(stars)} {tags}</div></div></div>')
    if e["body"]:
        with st.expander("Notes"):
            st.markdown(e["body"])
    c1, c2, c3, _ = st.columns([1, 1, 1, 4])
    if e["review_on"] and c1.button("Done reviewing", key=f"{key}-done-{e['id']}", type="tertiary"):
        journal.edit(uid, e["id"], review_on=None)
        st.rerun()
    if c2.button("Review in 3 months", key=f"{key}-later-{e['id']}", type="tertiary"):
        journal.edit(uid, e["id"], review_on=date.today() + timedelta(days=91))
        st.rerun()
    if c3.button("Delete", key=f"{key}-del-{e['id']}", type="tertiary"):
        journal.remove(uid, e["id"])
        st.rerun()


def page() -> None:
    user = st.session_state["user"]
    page_header("Journal", "Your reasons, decisions and lessons, company by company.")
    rows = journal.entries(user["id"])
    s = journal.stats(rows)
    stats = [("Entries", s["entries"]), ("Companies", s["companies"]),
             ("Average conviction", f"{s['avg_conviction']:.1f} / 5" if s["avg_conviction"] else "–"), ("Reviews due", s["reviews_due"])]
    html_block('<div class="stat-grid">' + "".join(f'<div class="stat"><div class="k">{k}</div><div class="v">{esc(v)}</div></div>'
                                                   for k, v in stats) + "</div>")
    with st.expander("New entry", icon=":material/edit_note:", expanded=not rows):
        entry_form(user["id"])
    due = [r for r in rows if r["review_on"] and r["review_on"] <= date.today()]
    if due:
        html_block('<div class="section">Reviews due</div>')
        for e in due:
            entry_card(e, key="due")
    c1, c2, c3 = st.columns([1.4, 1, 1.4])
    companies = sorted({(r["market"], r["ticker"]) for r in rows if r["ticker"]})
    names = {"All companies": None, **{f"{(get_company(m, t, resolve=False) or {}).get('name_en', t)} ({t})": (m, t) for m, t in companies}}
    pick = c1.selectbox("Company", list(names), key="j-company")
    kinds = c2.multiselect("Type", list(journal.KINDS), format_func=lambda k: journal.KINDS[k], key="j-kinds")
    text = c3.text_input("Search", placeholder="words or tags", key="j-text")
    shown = [r for r in rows if (names[pick] is None or (r["market"], r["ticker"]) == names[pick])
             and (not kinds or r["kind"] in kinds)
             and (not text.strip() or text.lower() in f"{r['title']} {r['body']} {r['tags']}".lower())]
    html_block(f'<div class="section">Timeline ({len(shown)})</div>')
    if not shown:
        html_block('<div class="empty">No entries yet. Start with your thesis for a company you hold: why you own it, '
                   'what would make you sell, and when to review it.</div>')
    for e in shown[:200]:
        entry_card(e, key="tl")
