from datetime import timedelta

import streamlit as st

from core.config import direct_fetch
from core.db import as_utc
from core.ui import chip_html, esc, html_block, logo_html, long_date, page_header, relative_time
from services import events as events_svc, personal, portfolio as portfolio_svc, watch
from services.classify import is_major
from services.classify import ORDER, categorize, label
from services.pipeline import filings_for, sync_company
from sources import get_source

from .components import add_company_form, cached_english_news, histories, render_filing, render_news

PERIODS = {"Today": 0, "Last 7 days": 7, "Last 30 days": 30}


def page() -> None:
    user = st.session_state["user"]
    wl = watch.get_watchlist(user["id"])
    markets = sorted({w["market"] for w in wl}) or ["KR"]
    today = max(get_source(m).today() for m in markets)

    if not wl:
        page_header(long_date(today), "Add the companies you follow and their filings will show up here each day.")
        add_company_form(user, key="today-empty")
        return

    errors, waiting = [], [w["name_en"] for w in wl if not w.get("last_synced")]
    if direct_fetch():
        with st.spinner("Checking regulators for new filings"):
            for w in wl:
                try:
                    sync_company(w["market"], w["ticker"])
                except Exception:
                    errors.append(w["name_en"])
    synced = [as_utc(w["last_synced"]) for w in wl if w.get("last_synced")]

    all_wl = wl
    choice = st.session_state.get("today-market") or "All"
    if len(markets) > 1 and choice != "All":
        wl = [w for w in wl if get_source(w["market"]).country == choice] or all_wl
        today = get_source(wl[0]["market"]).today()

    period = st.session_state.get("today-period") or "Today"
    rows = []
    for m in sorted({w["market"] for w in wl}):
        since = get_source(m).today() - timedelta(days=PERIODS[period])
        rows += filings_for([(w["market"], w["ticker"]) for w in wl if w["market"] == m], since)
    rows.sort(key=lambda r: (r["filed_date"], r["uid"]), reverse=True)
    for r in rows:
        r["category"] = categorize(r["title_en"], r["title_local"], r.get("price_sensitive"))

    n = len(wl)
    yours = "your company" if n == 1 else f"your {n} companies"
    companies_with = len({(r["market"], r["ticker"]) for r in rows})
    source_txt = yours if n == 1 else f"{companies_with} of {yours}"
    if period == "Today":
        noun = "filing" if len(rows) == 1 else "filings"
        subtitle = (f"{len(rows)} {noun} from {source_txt} today." if rows else
                    ("Your company hasn't filed yet today." if n == 1 else f"None of {yours} has filed yet today."))
    else:
        subtitle = f"{len(rows)} filings from {source_txt}, {period.lower()}."
    page_header(long_date(today), subtitle)
    dashboard(user, all_wl)

    main, side = st.columns([2.2, 1], gap="large")
    with main:
        cols = st.columns(4)
        cols[0].selectbox("Period", list(PERIODS), key="today-period")
        cols[1].selectbox("View", ["By time", "By company"], key="today-view")
        cols[2].selectbox("Sort", ["Newest first", "Most important first"], key="today-sort")
        if len(markets) > 1:
            cols[3].selectbox("Country", ["All"] + [get_source(m).country for m in markets], key="today-market")

        if st.session_state.get("today-sort") == "Most important first":
            from services.importance import score as importance_score
            rows.sort(key=lambda r: (-importance_score(r), r["filed_date"]), reverse=False)
        present = [k for k in ORDER if any(r["category"] == k for r in rows)]
        counts = {k: sum(r["category"] == k for r in rows) for k in present}
        if present:
            st.caption("Tap categories to filter (tap again to clear).")
            html_block(category_css(present))
            picked = st.pills("Categories", present, selection_mode="multi", key="today-cats",
                              format_func=lambda k: f"{label(k)} {counts.get(k, 0)}", label_visibility="collapsed") or []
            if picked:
                rows = [r for r in rows if r["category"] in picked]

        if errors:
            st.caption("Couldn't reach the regulator for: " + ", ".join(errors) + ". Showing saved filings.")
        notes = []
        if synced:
            notes.append(f"Last checked {relative_time(min(synced))}")
        if waiting:
            notes.append("loading filings for " + ", ".join(waiting))
        if notes:
            text = ". ".join(notes)
            st.caption(text[:1].upper() + text[1:] + ".")
        if not rows:
            html_block('<div class="empty">Nothing filed in this period. Filings usually arrive during '
                       'local business hours. Try a longer period above.</div>')

        stars = personal.starred(user["id"])
        hist = histories([(r["market"], r["ticker"]) for r in rows])
        label_country = len(markets) > 1 and choice == "All"
        if (st.session_state.get("today-view") or "By time") == "By company":
            groups: dict[tuple[str, str], list[dict]] = {}
            for r in rows:
                groups.setdefault((r["market"], r["ticker"]), []).append(r)
            for (m, t), items in groups.items():
                name = items[0]["company_name"]
                country = f'<span class="fl-tk">{esc(get_source(m).country)}</span>' if label_country else ""
                html_block(f'<div class="group-head">{logo_html(name, t, market=m)}{esc(name)}<span class="fl-tk">{esc(t)}</span>'
                           f'{country}<span class="count">{len(items)} filing{"s" if len(items) != 1 else ""}</span></div>')
                for r in items:
                    render_filing(r, key="today", show_company=False, hist=hist.get((m, t)), stars=stars)
        else:
            for r in rows:
                render_filing(r, key="today", country=get_source(r["market"]).country if label_country else "",
                              hist=hist.get((r["market"], r["ticker"])), stars=stars)
        for credit in sorted({get_source(r["market"]).attribution for r in rows} - {""}):
            st.caption(credit)

    with side:
        coming = events_svc.upcoming([(w["market"], w["ticker"]) for w in all_wl], days=30)
        if coming:
            html_block('<div class="section" style="margin-top:.2rem">Coming up</div>' + "".join(
                f'<div class="ev-row"><b>{e["event_date"]:%d %b}</b>{esc(e["label"])} <span>{esc(e["company_name"])}</span></div>'
                for e in coming[:8]))
        html_block('<div class="section" style="margin-top:.2rem">Headlines</div>')
        names = {"All companies": None, **{f'{w["name_en"]} ({w["ticker"]})': w for w in all_wl}}
        pick = names[st.selectbox("Headlines for", list(names), key="today-news-co", label_visibility="collapsed")]
        items = []
        if pick:
            items = cached_english_news(pick["name_en"], pick["ticker"], 15)
        else:
            for w in all_wl[:6]:
                items += cached_english_news(w["name_en"], w["ticker"], 3)[:2]
        items.sort(key=lambda n: n["published"].timestamp() if n.get("published") else 0, reverse=True)
        render_news(items[:10])


def dashboard(user: dict, wl: list[dict]) -> None:
    """Four tiles: portfolio, filings today, major filings today, and dates coming up this week."""
    from .portfolio import MASK, quick_summary
    tiles = []
    txs = portfolio_svc.list_transactions(user["id"])
    if txs:
        uid = user["id"]
        st.session_state["pf-hide"] = portfolio_svc.hide_amounts(uid)
        st.toggle("Hide amounts", key="pf-hide", help="Hides portfolio amounts here and on the Portfolio page.",
                  on_change=lambda: portfolio_svc.set_hide_amounts(uid, st.session_state["pf-hide"]))
        base = portfolio_svc.home_currency(user["id"]) if st.session_state.get("pf-mode") == "Home currency" else "USD"
        signature = f"{len(txs)}:{max(t['id'] for t in txs)}:{max(str(t['tx_date']) for t in txs)}"
        s = quick_summary(user["id"], base, signature)
        if s:
            hidden = portfolio_svc.hide_amounts(user["id"])
            value = f"{MASK} {base}" if hidden else f"{s['value']:,.0f} {base}"
            pct = s["today_pct"]
            move = "" if pct is None else f'<span class="mv {"up" if pct > 0 else "down" if pct < 0 else "flat"}">{pct * 100:+.2f}%</span>'
            amount = "" if hidden or s["today"] is None else f" ({s['today']:+,.0f})"
            week = s.get("week_pct")
            wk = "" if week is None else f' · 1W <span class="mv {"up" if week > 0 else "down" if week < 0 else "flat"}">{week * 100:+.2f}%</span>'
            tiles.append(("Portfolio", value, f"1D {move}{esc(amount)}{wk}"))
    todays = []
    for m in {w["market"] for w in wl}:
        todays += filings_for([(w["market"], w["ticker"]) for w in wl if w["market"] == m], get_source(m).today())
    major = [r for r in todays if is_major(r)]
    companies = len({(r["market"], r["ticker"]) for r in todays})
    tiles.append(("Filings today", f"{len(todays)}", f"from {companies} compan{'y' if companies == 1 else 'ies'}"))
    tiles.append(("Major today", f"{len(major)}", esc(major[0]["company_name"] + ": " + major[0]["title_en"])[:60] if major else "results, deals, dividends..."))
    soon = events_svc.upcoming([(w["market"], w["ticker"]) for w in wl], days=7)
    nxt = f'{soon[0]["event_date"]:%d %b} {esc(soon[0]["ticker"])} {esc(soon[0]["label"])}' if soon else "nothing announced"
    tiles.append(("Next 7 days", f"{len(soon)} date{'s' if len(soon) != 1 else ''}", nxt))
    html_block('<div class="dash">' + "".join(
        f'<div class="stat"><div class="k">{k}</div><div class="v">{esc(v)}</div><div class="sub">{sub}</div></div>'
        for k, v, sub in tiles) + "</div>")


def category_css(keys: list[str]) -> str:
    """Colour each category pill like its tag (pill order follows `keys`)."""
    from core.ui import CHIP, is_dark
    dark = is_dark()
    rules = []
    for i, k in enumerate(keys, 1):
        c = CHIP.get(k, CHIP["other"])
        bg, fg = (c[2], c[3]) if dark else (c[0], c[1])
        sel = f".st-key-today-cats button:nth-of-type({i})"
        rules.append(f"{sel}{{background:{bg} !important;color:{fg} !important;border:1px solid transparent !important;}}"
                     f"{sel}[kind$='Active'],{sel}[aria-pressed='true'],{sel}[aria-checked='true']"
                     f"{{border:2px solid {fg} !important;font-weight:700 !important;}}"
                     f"{sel} p{{color:{fg} !important;}}")
    return "<style>" + "".join(rules) + "</style>"
