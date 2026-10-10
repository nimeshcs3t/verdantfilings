import calendar as cal
from datetime import date, timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import ipos as ipo_svc

STATUS = {"filed": "Filed", "upcoming": "Upcoming", "priced": "Priced", "listed": "Listed"}
SECTOR_CHIP = {"Technology": "contract", "Healthcare": "dividend", "Financials": "buyback", "Industrials": "board",
               "Consumer": "capital", "Energy": "mna", "Materials & Mining": "ownership", "Real Estate": "periodic",
               "Communication": "meeting", "Utilities": "earnings", "SPAC / Blank check": "insider", "Other": "other"}


def _money(v, cur) -> str:
    if v is None:
        return "–"
    for size, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= size:
            return f"{v / size:,.1f}{unit} {cur or ''}".strip()
    return f"{v:,.0f} {cur or ''}".strip()


def _price(r) -> str:
    lo, hi, cur = r.get("price_low"), r.get("price_high"), r.get("currency") or ""
    if not lo and not hi:
        return "–"
    return f"{lo:,.2f} {cur}" if not hi or lo == hi else f"{lo:,.2f}–{hi:,.2f} {cur}"


def card(r: dict, key: str = "u", stars: set | None = None) -> None:
    sector = r.get("sector") or "Other"
    when = f'{r["listing_date"]:%a %d %b %Y}' if r.get("listing_date") else "date not set"
    move = ""
    if r.get("first_day") is not None:
        cls = "up" if r["first_day"] > 0 else "down"
        move = f'<span class="mv {cls}">First day {r["first_day"] * 100:+.1f}%</span>'
    local = f'<div class="fl-orig ko">{esc(r["name_local"])}</div>' if r.get("name_local") and r["name_local"] != r["name"] else ""
    facts = [("Listing", when), ("Price", _price(r)), ("Raising", _money(r.get("raise_amount"), r.get("currency"))),
             ("Market cap", _money(r.get("market_cap"), r.get("currency")))]
    if r.get("close_date"):
        facts.insert(1, ("Offer closes", f'{r["close_date"]:%d %b}'))
    links = []
    if r.get("doc_url"):
        links.append(f'<a href="{esc(r["doc_url"])}" target="_blank" rel="noopener noreferrer">{esc(r.get("doc_label") or "Official document")}</a>')
    if r.get("website"):
        links.append(f'<a href="{esc(r["website"])}" target="_blank" rel="noopener noreferrer">Website</a>')
    html_block(
        f'<div class="ipo"><div class="fl-co">{logo_html(r["name"], r.get("ticker") or r["name"], market=r["market"])}'
        f'{esc(r["name"])}<span class="fl-tk">{esc(r.get("ticker") or "")}</span><span class="fl-tk">{esc(r.get("country") or "")}</span>'
        f'<span class="chip c-{SECTOR_CHIP.get(sector, "other")}">{esc(sector)}</span>'
        f'<span class="fl-flag imp">{esc(STATUS.get(r.get("status"), r.get("status") or ""))}</span>{move}</div>{local}'
        f'<div class="ipo-facts">' + "".join(f'<span><b>{esc(k)}</b> {esc(v)}</span>' for k, v in facts) + "</div>"
        + (f'<div class="ipo-ov">{esc(r["overview"])}</div>' if r.get("overview") else "")
        + f'<div class="ipo-meta">{esc(r.get("exchange") or "")}{"  ·  " if links else ""}{"  ·  ".join(links)}</div></div>')
    user = st.session_state.get("user")
    if user and stars is not None:
        on = r["uid"] in stars
        if st.button("Following" if on else "Follow", key=f"ipo-star-{key}-{r['uid']}", type="tertiary",
                     icon=":material/notifications_active:" if on else ":material/notification_add:",
                     help="Get updates when it prices, sets or moves its listing date and lists; added to your watchlist after listing."):
            ipo_svc.toggle_star(user["id"], r["uid"])
            st.rerun()


def calendar_view(rows: list[dict]) -> None:
    today = date.today()
    if "ipo-month" not in st.session_state:
        st.session_state["ipo-month"] = (today.year, today.month)
    year, month = st.session_state["ipo-month"]
    c1, c2, c3 = st.columns([0.6, 2, 0.6], vertical_alignment="center")
    if c1.button("◀", key="ipo-prev", width="stretch"):
        st.session_state["ipo-month"] = (year - 1, 12) if month == 1 else (year, month - 1)
        st.rerun()
    c2.markdown(f'<div class="cal-title">{cal.month_name[month]} {year}</div>', unsafe_allow_html=True)
    if c3.button("▶", key="ipo-next", width="stretch"):
        st.session_state["ipo-month"] = (year + 1, 1) if month == 12 else (year, month + 1)
        st.rerun()
    by_day: dict[date, list[dict]] = {}
    for r in rows:
        for d, kind in ((r.get("listing_date"), "list"), (r.get("close_date"), "close")):
            if d:
                by_day.setdefault(d, []).append({**r, "_kind": kind})
    head = "".join(f"<div class='cal-dow'>{d}</div>" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    cells = []
    for week in cal.Calendar(firstweekday=0).monthdatescalendar(year, month):
        for d in week:
            classes = "cal-day" + (" out" if d.month != month else "") + (" today" if d == today else "")
            items = by_day.get(d, [])
            chips = "".join(
                f'<a class="cal-ev {"k-results" if e["_kind"] == "list" else "k-other"}" href="{esc(e.get("doc_url") or "#")}" '
                f'target="_blank" rel="noopener noreferrer" title="{esc(e["name"])} ({esc(e.get("country") or "")}): '
                f'{"listing" if e["_kind"] == "list" else "offer closes"}"><b>{esc(e.get("ticker") or e["name"][:10])}</b> '
                f'{"lists" if e["_kind"] == "list" else "closes"}</a>' for e in items[:4])
            more = f'<div class="cal-more">+{len(items) - 4} more</div>' if len(items) > 4 else ""
            cells.append(f'<div class="{classes}"><div class="cal-num">{d.day}</div>{chips}{more}</div>')
    html_block(f'<div class="cal-grid">{head}{"".join(cells)}</div>')
    month_rows = sorted([e for d, es in by_day.items() if d.year == year and d.month == month for e in es],
                        key=lambda e: e["listing_date"] if e["_kind"] == "list" else e["close_date"])
    html_block('<div class="cal-agenda">' + ("".join(
        f'<div class="ev-row"><b>{(e["listing_date"] if e["_kind"] == "list" else e["close_date"]):%a %d %b}</b>'
        f'{esc(e["name"])} <span>{esc(e.get("country") or "")}, {"listing" if e["_kind"] == "list" else "offer closes"}</span></div>'
        for e in month_rows) or "<div class=empty>No IPO dates this month yet.</div>") + "</div>")
    st.caption("Green: first trading day. Grey: offer or subscription closes. Click a date to open the official document.")


def page() -> None:
    page_header("IPOs", "New listings in the USA, South Korea, Japan, Hong Kong and Australia, from each market's own sources.")
    rows = ipo_svc.listing()
    if not rows:
        html_block('<div class="empty">No IPOs loaded yet. They load at the next background run (every few hours); '
                   'the admin can run Poll filings on GitHub to load them now.</div>')
        return
    c1, c2, c3 = st.columns([1.4, 1.6, 1])
    countries = sorted({r.get("country") or r["market"] for r in rows})
    pick_c = c1.multiselect("Country", countries, key="ipo-country", placeholder="All countries")
    sectors = sorted({r.get("sector") or "Other" for r in rows})
    pick_s = c2.multiselect("Sector", sectors, key="ipo-sector", placeholder="All sectors")
    text = c3.text_input("Search", key="ipo-q", placeholder="Name or ticker")
    shown = [r for r in rows if (not pick_c or (r.get("country") or r["market"]) in pick_c)
             and (not pick_s or (r.get("sector") or "Other") in pick_s)
             and (not text.strip() or text.lower() in f'{r["name"]} {r.get("ticker") or ""} {r.get("name_local") or ""}'.lower())]
    today = date.today()
    upcoming = sorted([r for r in shown if r.get("status") != "listed" and (not r.get("listing_date") or r["listing_date"] >= today)],
                      key=lambda r: (r.get("listing_date") is None, r.get("listing_date") or date.max, r["name"]))
    recent = sorted([r for r in shown if r.get("listing_date") and r["listing_date"] < today or r.get("status") == "listed"],
                    key=lambda r: r.get("listing_date") or date.min, reverse=True)
    week = [r for r in upcoming if r.get("listing_date") and r["listing_date"] <= today + timedelta(days=7)]
    html_block('<div class="stat-grid">' + "".join(
        f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in (("Upcoming", len(upcoming)), ("Listing in the next 7 days", len(week)), ("Listed recently", len(recent)),
                     ("Countries", len({r.get("country") for r in shown})))) + "</div>")
    user = st.session_state["user"]
    stars = ipo_svc.starred(user["id"])
    following = sorted([r for r in rows if r["uid"] in stars], key=lambda r: r.get("listing_date") or date.max)
    t1, t2, t3, t4 = st.tabs([f"Upcoming ({len(upcoming)})", "Calendar", f"Recently listed ({len(recent)})",
                              f"Following ({len(following)})"])
    with t1:
        for r in upcoming[:150]:
            card(r, "u", stars)
        if not upcoming:
            st.caption("No upcoming IPOs match these filters.")
    with t2:
        calendar_view(shown)
    with t3:
        for r in recent[:100]:
            card(r, "r", stars)
    with t4:
        if not following:
            st.caption("Follow an IPO to get updates when it prices, when its listing date is set or changes, and when it "
                       "lists. Once listed, it's added to your watchlist automatically (USA, Japan, Hong Kong, Australia).")
        for r in following:
            card(r, "f", stars)
        st.caption("Alerts for new IPOs by country and sector: Account, then Signals.")
    st.caption("Sources: Nasdaq IPO calendar and SEC EDGAR (USA), DART (Korea), JPX (Japan), HKEXnews (Hong Kong), "
               "ASX (Australia). Sector, market cap and overview are read from the official document by AI where "
               "available; check the document before relying on them. Not investment advice.")
