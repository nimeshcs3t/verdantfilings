import secrets

import streamlit as st

from core.auth import change_password, update_user
from core.session import logout
from core.ui import esc, html_block, page_header
from services import backup, emailer, personal, settings as settings_svc, telegram
from sources import configured_sources, visible_sources

TIMEZONES = ["UTC", "Asia/Seoul", "Asia/Tokyo", "Asia/Kolkata", "Asia/Singapore", "Asia/Hong_Kong", "Asia/Dubai",
             "Asia/Jerusalem", "Australia/Sydney", "Europe/London", "Europe/Warsaw", "Europe/Paris",
             "America/New_York", "America/Chicago", "America/Los_Angeles"]


def page() -> None:
    user = st.session_state["user"]
    page_header("Account", f"Signed in as {user['username']}. Plan: {user['plan'].capitalize()}.")

    st.subheader("Telegram alerts", divider=False)
    if not telegram.enabled():
        st.caption("Telegram alerts aren't set up on this site yet.")
    elif user.get("telegram_chat_id"):
        st.write(f"Alerts go to your Telegram chat ending in {str(user['telegram_chat_id'])[-4:]}.")
        c1, c2, _ = st.columns([1, 1, 2])
        if c1.button("Send a test alert", width="stretch"):
            ok = telegram.send_message(user["telegram_chat_id"], "Test alert: your filings alerts are connected.")
            (st.success if ok else st.error)("Test alert sent." if ok else "Telegram didn't accept the message.")
        with st.expander("Telegram commands"):
            st.markdown("Send these to the bot. Replies arrive within about 10 minutes.\n\n"
                        "- `/list`: your watchlist\n- `/today`: today's filings\n"
                        "- `/add 005930` or `/add apple`: follow a company (add a country if needed, e.g. `/add BHP Australia`)\n"
                        "- `/remove AAPL`: stop following\n- `/portfolio`: your holdings\n- `/price AAPL`: latest price\n"
                        "- `/help`: this list")
        if c2.button("Disconnect Telegram", width="stretch"):
            update_user(user["id"], telegram_chat_id=None, tg_link_code=None)
            st.rerun()
    else:
        code = user.get("tg_link_code")
        if not code:
            code = secrets.token_urlsafe(12)
            update_user(user["id"], tg_link_code=code)
        bot = telegram.bot_username()
        if not bot:
            st.error("The Telegram bot couldn't be reached. Check TELEGRAM_BOT_TOKEN.")
        else:
            st.write("1. Open the bot and press Start.  \n2. Come back here and confirm.")
            c1, c2, _ = st.columns([1, 1, 2])
            c1.link_button("Open the bot", f"https://t.me/{bot}?start={code}", width="stretch")
            if c2.button("I pressed Start", type="primary", width="stretch"):
                from core.auth import get_user
                linked = get_user(user["id"]).get("telegram_chat_id")   # the background job may have linked it
                chat_id = linked or telegram.find_chat_for_code(code)
                if chat_id:
                    update_user(user["id"], telegram_chat_id=chat_id, tg_link_code=None)
                    telegram.send_message(chat_id, "Connected. You'll get an alert here when a company on your watchlist files.")
                    st.rerun()
                else:
                    st.warning("No Start message found yet. Press Start in the bot chat, wait a moment, then try "
                               "again. If it still doesn't connect, it will connect by itself within 10 minutes.")

    alert_settings(user)
    keyword_settings(user)

    delivery_settings(user)
    data_section(user)

    st.subheader("Appearance", divider=False)
    st.caption("Dark mode follows your phone or computer setting. To choose yourself, open the ⋮ menu at the top "
               "right of the app, then Settings, then pick Light or Dark.")

    st.subheader("Change password", divider=False)
    with st.form("pw", clear_on_submit=True):
        old = st.text_input("Current password", type="password", autocomplete="current-password")
        new = st.text_input("New password", type="password", autocomplete="new-password")
        confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
        if st.form_submit_button("Change password"):
            if new != confirm:
                st.error("The new passwords don't match.")
            else:
                err = change_password(user["id"], old, new)
                if err:
                    st.error(err)
                else:
                    personal.end_all_sessions(user["id"])
                    st.success("Password changed. Other devices have been signed out.")

    st.divider()
    c1, c2, _ = st.columns([1, 1.4, 2])
    if c1.button("Sign out", width="stretch"):
        logout()
        st.rerun()
    if c2.button("Sign out on all devices", width="stretch"):
        personal.end_all_sessions(user["id"])
        logout()
        st.rerun()


def alert_settings(user: dict) -> None:
    st.subheader("Alerts", divider=False)
    prefs = personal.get_prefs(user["id"])
    with st.form("alert-prefs", border=False):
        mode = st.radio("How to receive filing alerts", ["Instant", "Daily digest"], horizontal=True,
                        index=0 if prefs["alert_mode"] == "instant" else 1,
                        help="Instant sends each filing as it arrives. Daily digest sends one message a day.")
        c1, c2 = st.columns(2)
        hour = c1.selectbox("Digest time", list(range(24)), index=int(prefs["digest_hour"]),
                            format_func=lambda h: f"{h:02d}:00")
        tz = c2.selectbox("Your timezone", TIMEZONES,
                          index=TIMEZONES.index(prefs["tz"]) if prefs["tz"] in TIMEZONES else 0)
        skip = st.checkbox("Skip insider-trade alerts (Form 4, director dealings)", value=bool(prefs["skip_insider"]))
        weekly = st.checkbox("Send a weekly report on Monday at the digest time", value=bool(prefs["weekly"]))
        if st.form_submit_button("Save alert settings", type="primary"):
            personal.save_prefs(user["id"], alert_mode="instant" if mode == "Instant" else "digest",
                                digest_hour=hour, tz=tz, skip_insider=skip, weekly=weekly)
            st.success("Saved.")
    st.caption("Per-company choices (All filings, Major only, Off) are on the Watchlist page.")


def keyword_settings(user: dict) -> None:
    st.subheader("Keyword alerts", divider=False)
    st.caption("Get a Telegram message when any filing title contains a word, even from companies you don't follow "
               "(Korea, USA, Poland and Japan are checked market-wide). Write keywords in English; they're matched "
               "in each market's language too.")
    markets = {"All countries": "*", **{s.country: s.market for s in visible_sources(user)}}
    with st.form("add-keyword", clear_on_submit=True, border=False):
        c1, c2, c3 = st.columns([2, 1.2, 0.8], vertical_alignment="bottom")
        word = c1.text_input("Keyword", placeholder="e.g. buyback, acquisition, rights offering")
        where = c2.selectbox("Country", list(markets))
        if c3.form_submit_button("Add", width="stretch"):
            err = personal.add_keyword(user["id"], word, markets[where])
            st.warning(err) if err else st.rerun()
    names = {v: k for k, v in markets.items()}
    for kw in personal.list_keywords(user["id"]):
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.markdown(f'<span class="chip c-buyback" style="margin-left:0">{esc(kw["keyword"])}</span>'
                    f'<span class="fl-tk">{esc(names.get(kw["market"], kw["market"]))}</span>', unsafe_allow_html=True)
        if c2.button("Remove", key=f"kw-{kw['id']}", type="tertiary"):
            personal.remove_keyword(user["id"], kw["id"])
            st.rerun()



def delivery_settings(user: dict) -> None:
    prefs = settings_svc.get(user["id"])
    st.subheader("Email", divider=False)
    if not emailer.enabled():
        st.caption("Email isn't set up for this app yet. The admin adds SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD "
                   "and SMTP_FROM to the secrets (for example a Gmail app password).")
    with st.form("email-settings", border=False):
        address = st.text_input("Email address", value=prefs.get("email") or "")
        c1, c2 = st.columns(2)
        alerts = c1.checkbox("Filing alerts by email", value=bool(prefs.get("email_alerts")))
        briefs = c2.checkbox("Digests, briefs and reports by email", value=bool(prefs.get("email_briefs", True)))
        if st.form_submit_button("Save email settings"):
            if address and not emailer.valid_address(address):
                st.warning("That email address doesn't look right.")
            else:
                settings_svc.save(user["id"], email=address.strip(), email_alerts=alerts, email_briefs=briefs)
                st.success("Saved.")
    if prefs.get("email") and emailer.enabled() and st.button("Send a test email"):
        ok = emailer.send(prefs["email"], "Verdant Filings test", "<p>Email delivery works.</p>")
        st.success("Sent. Check your inbox (and spam folder).") if ok else st.error("Couldn't send. Check the SMTP secrets.")

    st.subheader("Briefings and reports", divider=False)
    with st.form("brief-settings", border=False):
        c1, c2 = st.columns([1.4, 1])
        morning = c1.checkbox("Morning brief: overnight filings, your portfolio and today's dates", value=bool(prefs.get("morning_brief")))
        hour = c2.selectbox("Morning brief time", list(range(4, 12)), index=list(range(4, 12)).index(int(prefs.get("morning_hour") or 7)),
                            format_func=lambda h: f"{h:02d}:00", help="In the timezone set under Alerts.")
        weekly = st.checkbox("Weekly AI briefing on Mondays (uses your digest time)", value=bool(prefs.get("weekly_ai", True)))
        c3, c4 = st.columns([1.4, 1])
        monthly = c3.checkbox("Monthly PDF portfolio report on the 1st", value=bool(prefs.get("monthly_pdf", True)))
        theme = c4.selectbox("Report style", ["light", "dark"], index=1 if prefs.get("report_theme") == "dark" else 0,
                             format_func=str.title)
        if st.form_submit_button("Save briefings"):
            settings_svc.save(user["id"], morning_brief=morning, morning_hour=hour, weekly_ai=weekly, monthly_pdf=monthly,
                              report_theme=theme)
            if weekly:
                personal.save_prefs(user["id"], weekly=True)
            st.success("Saved.")

    st.subheader("Signals", divider=False)
    from sources import visible_sources
    markets = {s.country: s.market for s in visible_sources(user)}
    chosen = [c for c, m in markets.items() if m in (prefs.get("new_listing_markets") or [])]
    with st.form("signal-settings", border=False):
        insider = st.checkbox("Insider buying alerts: 2 or more insiders buying within 30 days, or a single US purchase "
                              "of $1M or more, for companies you follow", value=bool(prefs.get("insider_alerts", True)))
        listing = st.multiselect("New listing alerts for these markets", list(markets), default=chosen)
        from services.ipos import COUNTRY, SECTORS
        c1, c2 = st.columns(2)
        ipo_c = c1.multiselect("New IPO alerts: countries", list(COUNTRY.values()),
                               default=[c for c in prefs.get("ipo_alert_countries") or [] if c in COUNTRY.values()])
        ipo_s = c2.multiselect("New IPO alerts: sectors (empty = all)", SECTORS,
                               default=[s for s in prefs.get("ipo_alert_sectors") or [] if s in SECTORS])
        if st.form_submit_button("Save signals"):
            settings_svc.save(user["id"], insider_alerts=insider, new_listing_markets=[markets[c] for c in listing],
                              ipo_alert_countries=ipo_c, ipo_alert_sectors=ipo_s)
            st.success("Saved.")


def data_section(user: dict) -> None:
    st.subheader("Your data", divider=False)
    st.caption("Download everything you've entered: watchlist, transactions, cash, journal, notes, stars, alerts, fair "
               "values, goals and settings, as JSON plus CSV files for spreadsheets.")
    if st.button("Prepare backup", icon=":material/download:"):
        st.session_state["backup-zip"] = backup.export(user["id"])
    if st.session_state.get("backup-zip"):
        from datetime import date
        st.download_button("Download backup (.zip)", st.session_state["backup-zip"],
                           file_name=f"verdant-backup-{date.today()}.zip", mime="application/zip", type="primary")
