import calendar as cal
from datetime import date, timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import ipos as ipo_svc

STATUS = {"filed": "Filed", "upcoming": "Upcoming", "open": "Open now", "priced": "Priced", "listed": "Listed"}
BRIEF_SRC = {"document": "from the prospectus", "page": "from the listing page", "search": "from a web search"}
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


def _jload(text) -> dict:
    import json
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


def _window(r) -> str | None:
    o, c = r.get("open_date"), r.get("close_date")
    if o and c and o != c:
        return f"{o:%d %b}–{c:%d %b}" if o.month != c.month else f"{o:%d}–{c:%d %b}"
    if c:
        return f"closes {c:%d %b}"
    return None


def card(r: dict, key: str = "u", stars: set | None = None) -> None:
    sector = r.get("sector") or "Other"
    details = _jload(r.get("details"))
    est = " (est.)" if details.get("listing_estimated") else ""
    when = f'{r["listing_date"]:%a %d %b %Y}{est}' if r.get("listing_date") else "date not set"
    move = ""
    if r.get("first_day") is not None:
        cls = "up" if r["first_day"] > 0 else "down"
        move = f'<span class="mv {cls}">First day {r["first_day"] * 100:+.1f}%</span>'
    local = f'<div class="fl-orig ko">{esc(r["name_local"])}</div>' if r.get("name_local") and r["name_local"] != r["name"] else ""
    facts = [("Listing", when)]
    if _window(r):
        facts.append(("Subscription", _window(r)))
    facts += [("Price", _price(r)), ("Raising", _money(r.get("raise_amount"), r.get("currency")))]
    if r.get("shares_offered"):
        facts.append(("Shares offered", f'{r["shares_offered"]:,.0f}'))
    facts.append(("Market cap", _money(r.get("market_cap"), r.get("currency"))))
    if details.get("lot"):
        facts.append(("Lot size", details["lot"]))
    if details.get("announced") and not r.get("listing_date"):
        facts.append(("Announced", details["announced"]))
    if r.get("underwriters"):
        u = r["underwriters"]
        facts.append(("Lead managers", u if len(u) <= 60 else u[:57] + "…"))
    links = []
    if r.get("doc_url"):
        links.append(f'<a href="{esc(r["doc_url"])}" target="_blank" rel="noopener noreferrer">{esc(r.get("doc_label") or "Official document")}</a>')
    if r.get("website"):
        links.append(f'<a href="{esc(r["website"])}" target="_blank" rel="noopener noreferrer">Website</a>')
    for name, url in _jload(r.get("sources")).items():
        if url and name not in ("official",):
            links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(name)}</a>')
    status = r.get("status") or ""
    brief = (f'<div class="ipo-brief">{esc(r["overview"])}<span class="ipo-src"> {esc(BRIEF_SRC.get(r.get("brief_src") or "", ""))}</span></div>'
             if r.get("overview") else '<div class="ipo-brief none">No description yet; it\'s added at the next update if one can be found.</div>')
    html_block(
        f'<div class="ipo"><div class="fl-co">{logo_html(r["name"], r.get("ticker") or r["name"], market=r["market"])}'
        f'{esc(r["name"])}<span class="fl-tk">{esc(r.get("ticker") or "")}</span><span class="fl-tk">{esc(r.get("country") or "")}</span>'
        f'<span class="chip c-{SECTOR_CHIP.get(sector, "other")}">{esc(sector)}</span>'
        f'<span class="fl-flag imp{" open" if status == "open" else ""}">{esc(STATUS.get(status, status))}</span>{move}</div>{local}'
        + brief
        + f'<div class="ipo-facts">' + "".join(f'<span><b>{esc(k)}</b> {esc(v)}</span>' for k, v in facts) + "</div>"
        + '<div class="ipo-meta">' + "  ·  ".join(([esc(r["exchange"])] if r.get("exchange") else []) + links) + "</div></div>")
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


def _when(r) -> date:
    return r.get("listing_date") or r.get("close_date") or date.max


def page() -> None:
    page_header("Upcoming IPOs", "New listings across the USA, Europe, Asia and Australia, gathered from exchanges and "
                "IPO calendar websites, with a short brief on each company.")
    rows = ipo_svc.listing()
    if not rows:
        html_block('<div class="empty">No IPOs loaded yet. They load every 4 hours; the admin can run '
                   '<b>IPO calendar</b> on GitHub (Actions) to load them now.</div>')
        return
    today = date.today()
    c1, c2, c3, c4 = st.columns([1.3, 1.5, 1.1, 1])
    countries = sorted({r.get("country") or r["market"] for r in rows})
    pick_c = c1.multiselect("Country", countries, key="ipo-country", placeholder="All countries")
    sectors = sorted({r.get("sector") or "Other" for r in rows})
    pick_s = c2.multiselect("Sector", sectors, key="ipo-sector", placeholder="All sectors")
    stages = ["Open now", "Upcoming", "Priced", "Filed"]
    pick_st = c3.multiselect("Stage", stages, key="ipo-stage", placeholder="All stages")
    text = c4.text_input("Search", key="ipo-q", placeholder="Name or ticker")
    wanted = {k for k, v in STATUS.items() if v in pick_st}
    shown = [r for r in rows if (not pick_c or (r.get("country") or r["market"]) in pick_c)
             and (not pick_s or (r.get("sector") or "Other") in pick_s)
             and (not text.strip() or text.lower() in f'{r["name"]} {r.get("ticker") or ""} {r.get("name_local") or ""}'.lower())]
    upcoming = [r for r in shown if r.get("status") != "listed" and (not r.get("listing_date") or r["listing_date"] >= today
                                                                      or (r.get("close_date") and r["close_date"] >= today))]
    if wanted:
        upcoming = [r for r in upcoming if r.get("status") in wanted]
    upcoming.sort(key=lambda r: (r.get("status") != "open", _when(r) == date.max, _when(r), r["name"]))
    recent = sorted([r for r in shown if r not in upcoming and (r.get("status") == "listed" or (r.get("listing_date") and r["listing_date"] < today))],
                    key=lambda r: r.get("listing_date") or date.min, reverse=True)
    week = [r for r in upcoming if r.get("listing_date") and r["listing_date"] <= today + timedelta(days=7)]
    open_now = [r for r in upcoming if r.get("status") == "open"]
    html_block('<div class="stat-grid">' + "".join(
        f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in (("Upcoming", len(upcoming)), ("Open for subscription", len(open_now)),
                     ("Listing in the next 7 days", len(week)), ("Countries", len({r.get("country") for r in upcoming})))) + "</div>")
    user = st.session_state["user"]
    stars = ipo_svc.starred(user["id"])
    following = sorted([r for r in rows if r["uid"] in stars], key=_when)
    t1, t2, t3, t4 = st.tabs([f"Upcoming ({len(upcoming)})", "Calendar", f"Recently listed ({len(recent)})",
                              f"Following ({len(following)})"])
    with t1:
        if not upcoming:
            st.caption("No upcoming IPOs match these filters.")
        shown_n = st.session_state.get("ipo-more", 60)
        for r in upcoming[:shown_n]:
            card(r, "u", stars)
        if len(upcoming) > shown_n and st.button(f"Show more ({len(upcoming) - shown_n} left)", key="ipo-more-btn"):
            st.session_state["ipo-more"] = shown_n + 60
            st.rerun()
    with t2:
        calendar_view(shown)
    with t3:
        for r in recent[:100]:
            card(r, "r", stars)
        if not recent:
            st.caption("No recent listings match these filters.")
    with t4:
        if not following:
            st.caption("Follow an IPO to get updates when it prices, when its listing date is set or changes, and when it "
                       "lists. Once listed, it's added to your watchlist automatically (USA, Japan, Hong Kong, Australia).")
        for r in following:
            card(r, "f", stars)
        st.caption("Alerts for new IPOs by country and sector: Account, then Signals.")
    _sources_note()


def _sources_note() -> None:
    from services import status
    rows = status.ipo_sources()
    ok = [r for r in rows if r["state"] in ("ok", "warning")]
    with st.expander(f"Sources ({len(ok)} of {len(rows)} working)"):
        html_block('<div class="ipo-srcs">' + "".join(
            f'<span class="s-{r["state"]}">{esc(r["source"])} <small>{esc(r["country"])}'
            f'{" · " + str(r["count"]) if r["count"] else ""}</small></span>' for r in rows) + "</div>")
        st.caption("Official feeds: Nasdaq and SEC (USA), DART (Korea), JPX (Japan), HKEX (Hong Kong), ASX (Australia). "
                   "IPO websites: Iposcoop, Renaissance Capital, StockAnalysis (USA), Euronext (France, Netherlands, "
                   "Italy, Norway, Belgium, Portugal, Ireland), AAStocks (Hong Kong), IPO Watch (India), Kalkine "
                   "(Australia), IPO Kiso (Japan), e-IPO (Indonesia). The same IPO from several sources is shown once. "
                   "Briefs come from the prospectus, the listing page or a web search; check the official document "
                   "before relying on any detail. Not investment advice.")
