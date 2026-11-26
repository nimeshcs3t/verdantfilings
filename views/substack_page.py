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
    """Add newsletters one at a time; each one listed with a remove button."""
    with st.expander(f"Newsletters you follow ({len(follows)})", expanded=not follows, icon=":material/edit:"):
        st.caption("Add each newsletter you read. Any of these works: its name (thegeneralist), its address "
                   "(thegeneralist.substack.com or its own domain), a link to any of its posts, or the writer's "
                   "profile (@handle). Your own Substack account can stay private.")
        with st.form("ss-add", border=False, clear_on_submit=True):
            c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
            entry = c1.text_input("Add a newsletter", placeholder="thegeneralist, or paste a link", key="ss-entry")
            if c2.form_submit_button("Add", type="primary", width="stretch") and entry.strip():
                with st.spinner("Checking the newsletter"):
                    value, title, err = substack.resolve(entry)
                if err:
                    st.warning(err)
                elif value in follows:
                    st.info(f"{title} is already on your list.")
                else:
                    settings_svc.save(uid, substack_feeds=(follows + [value])[:150])
                    st.cache_data.clear()
                    st.toast(f"Added {title}.")
                    st.rerun()
        for n, item in enumerate(follows):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            url = substack.feed_url(item) or ""
            c1.markdown(f"[{esc(item)}]({url.removesuffix('/feed')})")
            if c2.button("Remove", key=f"ss-rm-{n}", width="stretch"):
                settings_svc.save(uid, substack_feeds=[f for f in follows if f != item])
                st.cache_data.clear()
                st.rerun()
        with st.popover("Add many at once", icon=":material/playlist_add:"):
            with st.form("ss-list", border=False):
                text = st.text_area("One per line", height=160, placeholder="thegeneralist\nnetinterest.co")
                if st.form_submit_button("Add all"):
                    added, bad = [], []
                    for line in [x.strip() for x in text.splitlines() if x.strip()][:100]:
                        value = substack.resolve(line)[0] if line.startswith("@") or "substack.com/@" in line else line
                        (added if value and substack.feed_url(value) else bad).append(value or line)
                    settings_svc.save(uid, substack_feeds=list(dict.fromkeys(follows + added))[:150])
                    st.cache_data.clear()
                    st.success(f"Added {len(added)}." + (f" Skipped: {', '.join(bad)}." if bad else ""))
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
