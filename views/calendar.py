import calendar as cal
from datetime import date, timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import events as events_svc, portfolio, watch

KINDS = {"results": "Results", "meeting": "Meetings", "dividend": "Dividends and record dates", "other": "Other dates"}


def page() -> None:
    user = st.session_state["user"]
    today = date.today()
    if "cal-month" not in st.session_state:
        st.session_state["cal-month"] = (today.year, today.month)
    year, month = st.session_state["cal-month"]
    page_header("Calendar", "Results releases, meetings and dividend dates announced in your companies' filings.")

    c1, c2, c3, c4 = st.columns([0.6, 1.6, 0.6, 0.9], vertical_alignment="center")
    if c1.button("◀", key="cal-prev", help="Previous month", width="stretch"):
        st.session_state["cal-month"] = (year - 1, 12) if month == 1 else (year, month - 1)
        st.rerun()
    c2.markdown(f'<div class="cal-title">{cal.month_name[month]} {year}</div>', unsafe_allow_html=True)
    if c3.button("▶", key="cal-next", help="Next month", width="stretch"):
        st.session_state["cal-month"] = (year + 1, 1) if month == 12 else (year, month + 1)
        st.rerun()
    if c4.button("This month", width="stretch"):
        st.session_state["cal-month"] = (today.year, today.month)
        st.rerun()

    pairs = [(w["market"], w["ticker"]) for w in watch.get_watchlist(user["id"])]
    pairs += [(h["market"], h["ticker"]) for h in portfolio.current_holdings(user["id"]) if (h["market"], h["ticker"]) not in pairs]
    picked = st.pills("Show", list(KINDS.values()), selection_mode="multi", default=list(KINDS.values()),
                      key="cal-kinds", label_visibility="collapsed")
    first = date(year, month, 1)
    last = date(year, month, cal.monthrange(year, month)[1])
    items = [e for e in events_svc.between(pairs, first - timedelta(days=7), last + timedelta(days=7))
             if KINDS[events_svc.kind(e["label"])] in (picked or [])]
    by_day: dict[date, list[dict]] = {}
    for e in items:
        by_day.setdefault(e["event_date"], []).append(e)

    weeks = cal.Calendar(firstweekday=0).monthdatescalendar(year, month)
    head = "".join(f"<div class='cal-dow'>{d}</div>" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    cells = []
    for week in weeks:
        for d in week:
            classes = "cal-day" + (" out" if d.month != month else "") + (" today" if d == today else "")
            chips = "".join(
                f'<a class="cal-ev k-{events_svc.kind(e["label"])}" href="{esc(e["url"])}" target="_blank" '
                f'rel="noopener noreferrer" title="{esc(e["company_name"])}: {esc(e["label"])}">'
                f'<b>{esc(e["ticker"])}</b> {esc(e["label"])}</a>' for e in by_day.get(d, [])[:4])
            more = f'<div class="cal-more">+{len(by_day[d]) - 4} more</div>' if len(by_day.get(d, [])) > 4 else ""
            cells.append(f'<div class="{classes}"><div class="cal-num">{d.day}</div>{chips}{more}</div>')
    html_block(f'<div class="cal-grid">{head}{"".join(cells)}</div>')

    month_items = [e for e in items if first <= e["event_date"] <= last]
    agenda = "".join(
        f'<div class="ev-row"><b>{e["event_date"]:%a %d %b}</b><span class="cal-dot k-{events_svc.kind(e["label"])}"></span>'
        f'{logo_html(e["company_name"], e["ticker"], market=e["market"])}{esc(e["company_name"])}: {esc(e["label"])} '
        f'<a href="{esc(e["url"])}" target="_blank" rel="noopener noreferrer">filing</a></div>' for e in month_items)
    html_block(f'<div class="cal-agenda">{agenda or "<div class=empty>No dates announced for this month yet.</div>"}</div>')
    st.caption("Dates come from filings that announce them (meeting notices, dividend decisions, results-date notices), "
               "for your watchlist and holdings. Companies that haven't announced a date yet won't show one.")
