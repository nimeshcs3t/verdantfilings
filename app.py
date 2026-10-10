"""Entry point: streamlit run app.py"""
import streamlit as st

from core.config import app_name

st.set_page_config(page_title=app_name(), page_icon=":material/description:", layout="wide",
                   initial_sidebar_state="collapsed")

from core import nav  # noqa: E402
from core.auth import ensure_admin  # noqa: E402
from core.db import get_engine  # noqa: E402
from core.session import apply_cookie_changes, current_user  # noqa: E402
from core.ui import inject_css  # noqa: E402
from views import account, admin, calendar, company, ipos, journal, portfolio, search, signin, today, watchlist  # noqa: E402


@st.cache_resource(show_spinner=False)
def bootstrap() -> bool:
    get_engine()
    ensure_admin()
    return True


bootstrap()
inject_css()
try:
    st.logo("assets/logo.svg", size="large")
except Exception:
    pass

# Read-only shared portfolio links work without signing in.
_share = st.query_params.get("share")
if _share:
    from services.share import owner
    _owner = owner(_share)
    if _owner:
        portfolio.share_page(_owner)
    else:
        st.error("This share link isn't valid any more.")
    st.stop()

user = current_user()
apply_cookie_changes()
if user is None:
    st.navigation([st.Page(signin.page, title="Sign in", url_path="signin", default=True)], position="hidden").run()
    st.stop()

st.session_state["user"] = user
pages = {
    "today": st.Page(today.page, title="Today", url_path="today", default=True),
    "company": st.Page(company.page, title="Companies", url_path="company"),
    "watchlist": st.Page(watchlist.page, title="Watchlist", url_path="watchlist"),
    "portfolio": st.Page(portfolio.page, title="Portfolio", url_path="portfolio"),
    "calendar": st.Page(calendar.page, title="Calendar", url_path="calendar"),
    "ipos": st.Page(ipos.page, title="IPOs", url_path="ipos"),
    "journal": st.Page(journal.page, title="Journal", url_path="journal"),
    "search": st.Page(search.page, title="Search", url_path="search"),
    "account": st.Page(account.page, title="Account", url_path="account"),
}
if user["role"] == "admin":
    pages["admin"] = st.Page(admin.page, title="Admin", url_path="admin")
nav.PAGES.clear()
nav.PAGES.update(pages)
current = st.navigation(list(pages.values()), position="top")

# Bottom menu for phones (hidden on wider screens by CSS); page links switch pages without reloading.
with st.container(key="bottomnav", horizontal=True, gap=None):
    for name, icon, label in (("today", ":material/today:", "Today"), ("company", ":material/domain:", "Companies"),
                              ("portfolio", ":material/donut_large:", "Portfolio"),
                              ("calendar", ":material/calendar_month:", "Calendar"), ("search", ":material/search:", "Search")):
        st.page_link(pages[name], label=label, icon=icon)

current.run()
