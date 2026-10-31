"""Widgets shared by several pages."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import streamlit as st

from core import nav
from core.db import as_utc, utcnow
from core.ui import esc, filing_row_html, html_block, news_html, summary_html
from core.config import direct_fetch
from services import ask, github, news, personal, prices, watch
from services.classify import categorize
from services.pipeline import enrich_filing, get_company, is_queued, listings_ready, search_companies
from sources import configured_sources, get_source, visible_sources


@st.cache_data(ttl=3600, show_spinner=False)
def price_history(market: str, ticker: str) -> list:
    try:
        return prices.history(market, ticker)
    except Exception:
        return []


def histories(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], list]:
    """Price histories for several companies at once (in parallel, cached for an hour)."""
    pairs = list(dict.fromkeys(pairs))[:40]
    if not pairs:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(pairs, pool.map(lambda p: price_history(*p), pairs)))


def render_filing(f: dict, key: str, show_company: bool = True, link_company: bool = True,
                  country: str = "", hist: list | None = None, stars: set | None = None, show_date: bool = True) -> None:
    with st.container(key=f"fil-{key}-{f['uid']}"):
        _render_filing(f, key, show_company, link_company, country, hist, stars, show_date)


def _render_filing(f: dict, key: str, show_company: bool, link_company: bool, country: str, hist: list | None,
                   stars, show_date: bool) -> None:
    created = as_utc(f.get("created_at"))
    is_new = bool(created and utcnow() - created < timedelta(hours=2))
    category = categorize(f.get("title_en"), f.get("title_local"), f.get("price_sensitive"))
    move = prices.move_html(prices.move_on(hist, f["filed_date"])) if hist else ""
    is_starred = bool(stars and f["uid"] in stars)
    mark = stars.get(f["uid"]) if isinstance(stars, dict) else ("important" if is_starred else None)
    from services.importance import score as importance_score
    n_notes = (getattr(stars, "notes", {}) or {}).get(f["uid"], 0)
    html_block(filing_row_html(f, show_company, is_new, country, category, move, is_starred,
                               important=importance_score(f) >= 75, mark=mark, notes=n_notes, show_date=show_date))
    user = st.session_state.get("user")
    # one quiet line under the filing: flags, journal note and the overview toggle (opens to full width)
    with st.container(horizontal=True, vertical_alignment="center", gap="small", key=f"frow-{key}-{f['uid']}"):
        if user:
            flag_key = f"flag-{key}-{f['uid']}"
            st.pills("Flag", list(personal.MARK_LABELS), selection_mode="single", default=mark, key=flag_key,
                     format_func=personal.MARK_LABELS.get, label_visibility="collapsed",
                     on_change=_save_flag, args=(user["id"], f["uid"], flag_key))
            with st.popover(f"Journal note ({n_notes})" if n_notes else "Journal note", icon=":material/history_edu:"):
                filing_note_form(user["id"], f, key, n_notes)
        with st.expander("Overview and translation" if f.get("title_local") and f.get("title_local") != f.get("title_en") else "Overview"):
            if f.get("summary_en"):
                _show_enriched(f)
                _ask_box(f, key)
            elif not direct_fetch() and is_queued(f["uid"]):
                st.caption("The overview is being prepared. It usually takes a few minutes; reload the page then.")
            elif st.button("Translate and summarize", key=f"enrich-{key}-{f['uid']}"):
                with st.spinner("Reading the filing"):
                    enriched = enrich_filing(f["uid"])
                if enriched and enriched.get("enrich_pending"):
                    github.trigger_sync()
                    st.success("Requested. The overview will be ready in a few minutes; reload the page then.")
                elif enriched and enriched.get("summary_en"):
                    _show_enriched(enriched)
                else:
                    st.warning((enriched or {}).get("enrich_error", "This filing couldn't be processed."))
            else:
                st.caption("Translates the filing to English and writes a short overview.")
            if show_company and link_company:
                if st.button("Company page", key=f"co-{key}-{f['uid']}", type="tertiary"):
                    nav.open_company(f["market"], f["ticker"])


def filing_note_form(uid: int, f: dict, key: str, n_notes: int = 0) -> None:
    """Write a journal note on this filing. It's saved to the journal under the company, linked to the filing."""
    from datetime import date
    from services import journal
    if n_notes:
        for e in journal.entries(uid, filing_uid=f["uid"])[:5]:
            html_block(f'<div class="jn-prev"><b>{e["entry_date"]:%d %b %Y}</b> {esc(journal.KINDS.get(e["kind"], ""))}'
                       f'{": " + esc(e["title"]) if e.get("title") else ""}'
                       + (f'<div>{esc((e["body"] or "")[:400])}</div>' if e.get("body") else "") + "</div>")
        st.caption("All notes are on the Journal page and in the company's Journal section.")
    with st.form(f"jn-{key}-{f['uid']}", clear_on_submit=True, border=False):
        kinds = ["filing", "update", "thesis", "buy", "sell", "review", "lesson"]
        kind = st.selectbox("Type", kinds, format_func=lambda k: journal.KINDS[k])
        title = st.text_input("Title", placeholder="e.g. Guidance raised; margins better than I expected")
        body = st.text_area("Note", height=120, placeholder="What this filing changes for your view...")
        c1, c2 = st.columns(2)
        target = c1.number_input("Price target", min_value=0.0, value=0.0, step=1.0, help="Optional; drawn on the price chart.")
        review = c2.date_input("Review on", value=None, min_value=date.today())
        if st.form_submit_button("Save to journal", type="primary"):
            err = journal.add(uid, f["market"], f["ticker"], date.today(), kind, title or f"On: {f.get('title_en') or ''}"[:200],
                              body, None, "", review, target or None, filing_uid=f["uid"])
            if err:
                st.warning(err)
            else:
                st.toast("Saved to your journal")
                st.rerun()


def _save_flag(user_id: int, uid: str, widget_key: str) -> None:
    personal.set_flag(user_id, uid, st.session_state.get(widget_key))


def _ask_box(f: dict, key: str) -> None:
    if not ask.available():
        return
    with st.form(f"ask-{key}-{f['uid']}", border=False, clear_on_submit=False):
        cols = st.columns([5, 1], vertical_alignment="bottom")
        question = cols[0].text_input("Ask about this filing", placeholder="e.g. What is the deal size?",
                                      key=f"q-{key}-{f['uid']}")
        asked = cols[1].form_submit_button("Ask", width="stretch")
    store = f"answer-{key}-{f['uid']}"
    if asked and question.strip():
        with st.spinner("Reading the filing"):
            st.session_state[store] = ask.answer(question, f)
    if st.session_state.get(store):
        html_block(f'<div class="answer">{esc(st.session_state[store])}</div>')


def _show_enriched(f: dict) -> None:
    html_block(summary_html(f["summary_en"]))
    try:
        from services.notes import get as get_notes
        extra = get_notes(f["uid"])
    except Exception:
        extra = {}
    if extra.get("highlights"):
        st.caption("Highlights (AI, from this report)")
        html_block(summary_html(extra["highlights"]))
    if extra.get("changes"):
        st.caption("What changed since the previous report (AI)")
        html_block(summary_html(extra["changes"]))
    if f.get("body_en"):
        st.caption("Machine translation of the opening section")
        html_block(f'<div class="body-en">{esc(f["body_en"])}</div>')


def add_company_form(user: dict, key: str = "add") -> None:
    sources = visible_sources(user)
    if not sources:
        st.error("No regulator is connected yet. Add DART_API_KEY to the app secrets.")
        return
    with st.form(f"form-{key}", border=False):
        cols = st.columns([1, 3, 1], vertical_alignment="bottom") if len(sources) > 1 else st.columns([4, 1], vertical_alignment="bottom")
        if len(sources) > 1:
            market = cols[0].selectbox("Market", [s.market for s in sources],
                                       format_func=lambda m: get_source(m).country)
            query_col, btn_col = cols[1], cols[2]
        else:
            market, query_col, btn_col = sources[0].market, cols[0], cols[1]
        query = query_col.text_input("Add a company", placeholder=f"Ticker or name, e.g. 005930 or Samsung",
                                     key=f"q-{key}")
        submitted = btn_col.form_submit_button("Add", type="primary", width="stretch")
    if submitted and query.strip():
        src = get_source(market)
        ticker = src.normalize_ticker(query)
        try:
            known = get_company(market, ticker) if ticker else None
        except Exception:
            known = None
        if known:
            _add(user, market, known["ticker"])
        else:
            with st.spinner("Searching"):
                try:
                    found = search_companies(market, query)
                except Exception:
                    found = []
                st.session_state[f"matches-{key}"] = [(c.market, c.ticker, c.name_en, c.name_local) for c in found]
            if not st.session_state[f"matches-{key}"]:
                if not direct_fetch() and not get_source(market).resolve_in_app and not listings_ready(market):
                    st.warning("The company list is still loading. Try again in a few minutes.")
                else:
                    st.warning(f"No listed company matches “{query.strip()}”.")
    matches = st.session_state.get(f"matches-{key}") or []
    if matches:
        st.caption("Choose a company")
        for m, t, name_en, name_local in matches:
            label = f"{name_en}  ({t})" + (f"  {name_local}" if name_local and name_local != name_en else "")
            if st.button(label, key=f"pick-{key}-{m}-{t}", type="tertiary"):
                st.session_state.pop(f"matches-{key}", None)
                _add(user, m, t)


def _add(user: dict, market: str, ticker: str) -> None:
    with st.spinner("Adding company"):
        try:
            comp, err = watch.add(user, market, ticker)
        except Exception:
            comp, err = None, "The company couldn't be added right now. Try again in a few minutes."
    if err:
        st.warning(err)
    else:
        if not direct_fetch() and not comp.get("last_synced"):
            github.trigger_sync()
            st.toast(f"Added {comp['name_en']}. Its filings will appear within a few minutes.")
        else:
            st.toast(f"Added {comp['name_en']}")
        st.rerun()


@st.cache_data(ttl=1800, show_spinner=False)
def cached_english_news(name_en: str, ticker: str, limit: int = 10) -> list[dict]:
    return news.english_news(name_en, ticker, limit)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_local_news(market: str, name_local: str, limit: int = 10) -> list[dict]:
    src = get_source(market)
    if not src or not src.news_local:
        return []
    return news.local_news(name_local, src.news_local, src.source_lang, limit)


@st.cache_data(ttl=10800, show_spinner=False)
def cached_substack(name_en: str, ticker: str, newsletters: tuple = ()) -> dict:
    from services import substack
    return substack.lookup(name_en, ticker, list(newsletters))


def render_substack(result) -> None:
    posts = result["posts"] if isinstance(result, dict) else result
    if not posts:
        if isinstance(result, dict) and not result.get("search_reached"):
            msg = ("Substack's search couldn't be reached from the app's server"
                   + (f" ({esc(result['search_error'])})" if result.get("search_error") else "") + ". ")
            msg += ("Your followed newsletters had no posts about this company." if result.get("followed") else
                    "Add newsletters you read under Account, then Substack: those are read directly and still work.")
        else:
            msg = ("No Substack posts about this company in the last 3 months. Add newsletters you read under "
                   "Account, then Substack, to include their posts.")
        html_block(f'<div class="empty">{msg}</div>')
        return
    rows = []
    for p in posts:
        when = p["published"].strftime("%d %b %Y") if p.get("published") else ""
        tags = ('<span class="ss-tag mine">Following</span>' if p.get("from") == "followed" else "") + \
               ('<span class="ss-tag paid">Paid</span>' if p.get("paid") else "")
        rows.append(f'<div class="ss-post"><a href="{esc(p["link"])}" target="_blank" rel="noopener noreferrer">{esc(p["title"])}</a>{tags}'
                    + (f'<div class="ss-sum">{esc(p["summary"])}</div>' if p.get("summary") else "")
                    + f'<div class="ss-meta">{esc(p.get("source") or "Substack")}{" · " + when if when else ""}</div></div>')
    html_block("".join(rows))
    st.caption("From Substack's search and the newsletters you follow; posts are matched by company name or ticker, "
               "so an occasional one may be off-topic. Opinions are the authors' own.")


def render_news(items: list[dict]) -> None:
    if items:
        html_block(news_html(items))
    else:
        html_block('<div class="empty">No recent articles found.</div>')


def group_picker(user_id: int, market: str, ticker: str, key: str) -> None:
    """Choose this company's sector and structural themes (inside a popover or expander)."""
    from services import groups
    mine = groups.list_groups(user_id)
    sectors = [g for g in mine if g["kind"] == "sector"]
    themes = [g for g in mine if g["kind"] == "theme"]
    current = groups.assignments(user_id).get((market, ticker)) or {"sector": None, "themes": []}
    if not mine:
        st.caption("You haven't made any sectors or themes yet.")
    names = {g["id"]: g["name"] for g in mine}
    with st.form(f"grp-{key}", border=False):
        sector_ids = [None] + [g["id"] for g in sectors]
        cur_sector = (current["sector"] or {}).get("id")
        sector = st.selectbox("Sector", sector_ids, index=sector_ids.index(cur_sector) if cur_sector in sector_ids else 0,
                              format_func=lambda i: "None" if i is None else names[i],
                              help="What the company does. One per company.")
        chosen = st.multiselect("Structural themes", [g["id"] for g in themes],
                                default=[t["id"] for t in current["themes"] if t["id"] in names],
                                format_func=names.get, placeholder="Choose themes",
                                help="Long-running trends the company rides. A company can have several.")
        new_name = st.text_input("Or add a new one", placeholder="e.g. Defence electronics, Ageing population",
                                 max_chars=60)
        new_kind = st.radio("It is a", list(groups.KINDS), format_func=groups.KINDS.get, horizontal=True,
                            label_visibility="collapsed")
        if st.form_submit_button("Save", type="primary"):
            if new_name.strip():
                err = groups.add_group(user_id, new_name, new_kind)
                if err:
                    st.warning(err)
                    return
                added = next(g for g in groups.list_groups(user_id, new_kind)
                             if g["name"].lower() == " ".join(new_name.split()).lower())
                if new_kind == "sector":
                    sector = added["id"]
                else:
                    chosen = list(chosen) + [added["id"]]
            groups.set_company(user_id, market, ticker, sector, list(chosen))
            st.toast("Saved sector and themes.")
            st.rerun()
