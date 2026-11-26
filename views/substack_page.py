"""Substack: every newsletter you follow in one reading list. Posts that mention a company on your watchlist or in
your portfolio are marked."""
from __future__ import annotations

import streamlit as st

from core.ui import esc, html_block, page_header
from services import settings as settings_svc
from services import substack

PERIODS = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}


@st.cache_data(ttl=1800, show_spinner=False)
def _posts(newsletters: tuple, days: int) -> list[dict]:
    return substack.all_posts(list(newsletters), days)


def _manage(uid: int, follows: list[str]) -> None:
    with st.expander(f"Newsletters you follow ({len(follows)})", expanded=not follows, icon=":material/edit:"):
        st.caption("Bring in the list from your Substack profile, or paste names: one per line, the part before "
                   ".substack.com (e.g. thegeneralist) or a newsletter's own web address.")
        c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
        handle = c1.text_input("Your Substack handle", placeholder="@yourname", key="ss-handle")
        if c2.button("Import my subscriptions", key="ss-import", width="stretch") and handle.strip():
            with st.spinner("Reading your Substack profile"):
                found, err = substack.subscriptions_of(handle)
            if err:
                st.warning(err)
            else:
                merged = list(dict.fromkeys(follows + found))[:150]
                settings_svc.save(uid, substack_feeds=merged)
                st.cache_data.clear()
                st.success(f"Added {len(merged) - len(follows)} newsletters ({len(merged)} in all).")
                st.rerun()
        with st.form("ss-list", border=False):
            text = st.text_area("Newsletters", value="\n".join(follows), height=180, label_visibility="collapsed",
                                placeholder="thegeneralist\nnetinterest.co")
            if st.form_submit_button("Save list"):
                items = [line.strip() for line in text.splitlines() if line.strip()][:150]
                good = [i for i in items if substack.feed_url(i)]
                bad = [i for i in items if not substack.feed_url(i)]
                settings_svc.save(uid, substack_feeds=good)
                st.cache_data.clear()
                st.success(f"Saved {len(good)} newsletters." + (f" Skipped: {', '.join(bad)}." if bad else ""))
                st.rerun()


def page() -> None:
    user = st.session_state["user"]
    uid = user["id"]
    page_header("Substack", "Every newsletter you follow, in one reading list")
    follows = list(settings_svc.get(uid).get("substack_feeds") or [])
    _manage(uid, follows)
    if not follows:
        return
    c1, c2, c3 = st.columns([1, 1.4, 1.4])
    days = PERIODS[c1.selectbox("Period", list(PERIODS), index=1, key="ss-period")]
    with st.spinner(f"Reading {len(follows)} newsletters"):
        posts = _posts(tuple(follows), days)
    pubs = sorted({p["source"] for p in posts})
    pick = c2.multiselect("Newsletters", pubs, placeholder="All newsletters", key="ss-pubs")
    q = c3.text_input("Search", placeholder="words in title or text", key="ss-q")
    from .filings import _pairs
    mine = _pairs(uid, ["watchlist", "portfolio"])
    tests = [(name, substack.matcher(name, t)) for (m, t), name in mine.items()]
    only_mine = st.toggle("Only posts that mention my companies", key="ss-mine")
    shown = []
    for p in posts:
        if pick and p["source"] not in pick:
            continue
        text = f'{p["title"]} {p["summary"]} {p["body"][:8000]}'
        if q.strip() and q.strip().lower() not in text.lower():
            continue
        about = [name for name, test in tests if test(text)][:3]
        if only_mine and not about:
            continue
        shown.append((p, about))
    st.caption(f"{len(shown)} posts from {len({p['source'] for p, _ in shown})} newsletters.")
    rows = []
    for p, about in shown[:300]:
        when = p["published"].strftime("%d %b") if p.get("published") else ""
        tags = "".join(f'<span class="about">{esc(a)}</span>' for a in about)
        rows.append(f'<div class="ss-row"><div class="d">{when}</div><div><a href="{esc(p["link"])}" target="_blank" '
                    f'rel="noopener noreferrer">{esc(p["title"])}</a>{tags}<div class="pub">{esc(p["source"])}</div>'
                    + (f'<div class="sum">{esc(p["summary"][:220])}</div>' if p.get("summary") else "") + '</div></div>')
    html_block("".join(rows) or '<div class="empty">No posts in this period.</div>')
