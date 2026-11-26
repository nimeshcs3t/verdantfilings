"""Bank reports: free research from the sell side in one place, with each house's latest pieces."""
from __future__ import annotations

import streamlit as st

from core.ui import esc, html_block, page_header
from services import research


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _latest() -> dict:
    return research.all_latest()


def page() -> None:
    page_header("Bank reports", "Free research the sell-side banks publish on their own sites. Some of it is in-depth "
                                "and worth a read")
    groups = ["All"] + list(dict.fromkeys(b[0] for b in research.BANKS))
    c1, c2 = st.columns([2, 1])
    group = c1.segmented_control("Show", groups, default="All", key="bank-group") or "All"
    q = c2.text_input("Search titles", placeholder="e.g. AI capex, Korea, defence", key="bank-q",
                      label_visibility="collapsed")
    with st.spinner("Checking each research site for new pieces"):
        latest = _latest()
    banks = [b for b in research.BANKS if group == "All" or b[0] == group]
    word = q.strip().lower()
    if word:                                     # one list across every bank
        hits = [(name, i) for _, name, _, _ in banks for i in latest.get(name, []) if word in i["title"].lower()]
        if not hits:
            st.caption("No recent titles match. The banks' own sites have more than the latest few shown here.")
        rows = "".join(f'<div class="news"><a href="{esc(i["link"])}" target="_blank" rel="noopener noreferrer">'
                       f'{esc(i["title"])}</a><div class="news-meta">{esc(name)}'
                       f'{" · " + i["published"].strftime("%d %b %Y") if i.get("published") else ""}</div></div>'
                       for name, i in hits)
        html_block(rows)
        return
    cols = st.columns(3, gap="small")
    for n, (grp, name, url, _) in enumerate(banks):
        items = latest.get(name, [])
        body = "".join(f'<div class="bk-item"><a href="{esc(i["link"])}" target="_blank" rel="noopener noreferrer">'
                       f'{esc(i["title"])}</a><span>{i["published"].strftime("%d %b") if i.get("published") else ""}</span></div>'
                       for i in items[:5]) or '<div class="bk-none">No recent pieces found automatically. Open the site.</div>'
        with cols[n % 3].container(key=f"bank-{n}"):
            html_block(f'<div class="bk"><div class="bk-h"><b>{esc(name)}</b><span>{esc(grp)}</span></div>{body}'
                       f'<a class="bk-open" href="{esc(url)}" target="_blank" rel="noopener noreferrer">Open research site ↗</a></div>')
    st.caption("Latest pieces come from each site's feed where it has one, otherwise from Google News for that site, "
               "checked every 6 hours. Some research needs a free sign-up on the bank's site.")
