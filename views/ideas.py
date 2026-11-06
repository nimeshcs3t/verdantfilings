"""Stock ideas: a table to compare the companies you're considering (figures, your call, where the idea came from),
plus ways to add ideas from Twitter/X, Substack posts and funds' quarterly letters."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core.ui import esc, html_block, page_header
from services import groups, ideas as svc, journal

UP, DOWN = "#1B7A47", "#B4321F"


@st.cache_data(ttl=1800, show_spinner=False)
def _metrics(market: str, ticker: str) -> dict:
    out = svc.metrics(market, ticker)
    out["insiders"] = svc.insider_net(market, ticker)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _next_reports(pairs: tuple[tuple[str, str], ...]) -> dict:
    return svc.next_reports(list(pairs))


def _source_label(s: dict) -> str:
    kind = svc.SOURCE_KINDS.get(s["kind"], "Other")
    return f"{kind}: {s['author']}" if s.get("author") else kind


def _table(user: dict, rows: list[dict]) -> pd.DataFrame:
    uid = user["id"]
    sectors = groups.assignments(uid)
    nexts = _next_reports(tuple((r["market"], r["ticker"]) for r in rows))
    note_n: dict[tuple[str, str], int] = {}
    for e in journal.entries(uid):
        if e.get("market"):
            note_n[(e["market"], e["ticker"])] = note_n.get((e["market"], e["ticker"]), 0) + 1
    data = []
    bar = st.progress(0.0, text="Loading figures") if len(rows) > 6 else None
    for i, r in enumerate(rows, 1):
        key = (r["market"], r["ticker"])
        m = _metrics(*key)
        if bar:
            bar.progress(i / len(rows), text="Loading figures")
        decision = r.get("decision") or ""
        since = None
        if decision and r.get("decision_price") and m.get("price"):
            since = (m["price"] / r["decision_price"] - 1) * 100
        nxt = nexts.get(key)
        first = (r["sources"] or [{}])[0]
        pct = (lambda v: round(v * 100, 1) if v is not None else None)
        data.append({
            "#": i, "Ticker": r["ticker"], "Company": r.get("name") or r["ticker"],
            "Type": svc.TYPES.get(r.get("lynch_type") or "", ""),
            "Sector": ((sectors.get(key) or {}).get("sector") or {}).get("name", ""),
            "Price": m.get("price"), "1D": pct(m.get("d1")), "1M": pct(m.get("m1")), "12M": pct(m.get("m12")),
            "From high": pct(m.get("from_high")), "Mkt cap": m.get("market_cap"),
            "P/E": round(m["pe"], 1) if m.get("pe") else None, "EBIT %": pct(m.get("ebit_margin")),
            "Rev y/y": pct(m.get("growth")),
            "Next report": (nxt[0].strftime("%d %b") + (" (est.)" if nxt[1] else "")) if nxt else "",
            "Insiders 12m": m.get("insiders"), "Notes": note_n.get(key, 0) or None,
            "Source": _source_label(first) if first else "", "Sources": len(r["sources"]),
            "Link": first.get("url") or None,
            "Decision": (f"{decision} {since:+.1f}%" if since is not None else decision),
            "Status": svc.STATUSES.get(r.get("status") or "new", ""),
            "Market": r["market"], "_id": r["id"],
        })
    if bar:
        bar.empty()
    df = pd.DataFrame(data)
    for col in ("Price", "1D", "1M", "12M", "From high", "Mkt cap", "P/E", "EBIT %", "Rev y/y", "Insiders 12m", "Notes"):
        df[col] = pd.to_numeric(df[col], errors="coerce")          # empty values show as blanks, not "None"
    return df


def _style(df: pd.DataFrame):
    def sign(v):
        if v is None or (isinstance(v, float) and pd.isna(v)) or v == 0:
            return ""
        return f"color: {UP if v > 0 else DOWN}"

    def call(v):
        v = str(v or "")
        if v.startswith("BUY"):
            return f"color: {UP}; font-weight: 600"
        if v.startswith("SELL"):
            return f"color: {DOWN}; font-weight: 600"
        return "font-weight: 600" if v else ""
    shown = df.drop(columns=["_id"])
    return (shown.style
            .map(sign, subset=["1D", "1M", "12M", "From high", "Insiders 12m"]).map(call, subset=["Decision"]))


def ideas_tab(user: dict) -> None:
    rows = svc.list_ideas(user["id"])
    if not rows:
        html_block('<div class="empty">No ideas yet. Add one in <b>Add ideas</b>: a ticker and where you saw it, or '
                   'paste a post or a fund letter and pick the companies it mentions.</div>')
        return
    c1, c2, c3 = st.columns([1.3, 1.6, 1.6])
    q = c1.text_input("Find", placeholder="Name, ticker, source", label_visibility="collapsed")
    status = c2.pills("Status", list(svc.STATUSES), selection_mode="multi", format_func=svc.STATUSES.get,
                      default=[k for k in svc.STATUSES if k != "passed"], label_visibility="collapsed", key="idea-status")
    kinds = c3.pills("From", list(svc.SOURCE_KINDS)[:4], selection_mode="multi", format_func=svc.SOURCE_KINDS.get,
                     label_visibility="collapsed", key="idea-kinds")
    if status:
        rows = [r for r in rows if (r.get("status") or "new") in status]
    if kinds:
        rows = [r for r in rows if any(s["kind"] in kinds for s in r["sources"])]
    if q.strip():
        t = q.lower().strip()
        rows = [r for r in rows if t in " ".join([r["ticker"], r.get("name") or "", r.get("thesis") or ""] +
                                                 [f"{s.get('author') or ''} {s.get('note') or ''}" for s in r["sources"]]).lower()]
    if not rows:
        st.caption("No ideas match these filters.")
        return
    df = _table(user, rows)
    pct = "%+.1f%%"
    event = st.dataframe(
        _style(df), hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="idea-table",
        height=min(35 * (len(df) + 1) + 3, 720),
        column_order=["#", "Ticker", "Company", "Type", "Sector", "Price", "1D", "1M", "12M", "From high", "Mkt cap",
                      "P/E", "EBIT %", "Rev y/y", "Next report", "Insiders 12m", "Notes", "Source", "Link", "Decision"],
        column_config={
            "#": st.column_config.NumberColumn(width=40), "Ticker": st.column_config.TextColumn(width="small"),
            "Company": st.column_config.TextColumn(width="medium"), "Type": st.column_config.TextColumn(width="small"),
            "Price": st.column_config.NumberColumn(format="%.2f"),
            "1D": st.column_config.NumberColumn(format=pct), "1M": st.column_config.NumberColumn(format=pct),
            "12M": st.column_config.NumberColumn(format=pct), "From high": st.column_config.NumberColumn(format=pct),
            "Mkt cap": st.column_config.NumberColumn(format="compact", help="In the company's trading currency"),
            "P/E": st.column_config.NumberColumn(format="%.1fx"), "EBIT %": st.column_config.NumberColumn(format="%.1f%%",
                                                                                                        help="Operating margin, last full year"),
            "Rev y/y": st.column_config.NumberColumn(format=pct, help="Revenue growth, last full year"),
            "Next report": st.column_config.TextColumn(help="Announced results date, or an estimate from the company's rhythm"),
            "Insiders 12m": st.column_config.NumberColumn(format="compact", help="Value bought minus sold by insiders, 12 months"),
            "Notes": st.column_config.NumberColumn(help="Your journal entries on the company"),
            "Link": st.column_config.LinkColumn(display_text="Open", width="small"),
            "Decision": st.column_config.TextColumn(help="Your call, and the price change since you made it"),
        })
    st.caption("Click a row to see its sources, add notes or record a decision. Figures load from the app's price and "
               "filings data; blanks mean the app has no figure for that company.")
    sel = event.selection.rows if event and event.selection else []
    if sel:
        detail(user, int(df.iloc[sel[0]]["_id"]))


def detail(user: dict, idea_id: int) -> None:
    from core import nav
    uid = user["id"]
    idea = svc.get_idea(uid, idea_id)
    if not idea:
        return
    m = _metrics(idea["market"], idea["ticker"])
    with st.container(border=True, key=f"idea-detail-{idea_id}"):
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        c1.markdown(f"### {esc(idea.get('name') or idea['ticker'])} <span class='fl-tk'>{esc(idea['ticker'])}</span>",
                    unsafe_allow_html=True)
        if idea["market"] != "X":
            if c2.button("Open company page", key=f"idea-open-{idea_id}", width="stretch"):
                st.session_state["company_sel"] = (idea["market"], idea["ticker"])
                nav.go("company")

        left, right = st.columns([1.4, 1], gap="large")
        with left:
            html_block('<div class="section">Where it came from</div>')
            for s in idea["sources"]:
                a, b = st.columns([6, 1], vertical_alignment="center")
                link = f" [link]({s['url']})" if s.get("url") else ""
                a.markdown(f"**{esc(_source_label(s))}**, {s['source_date']:%d %b %Y}{link}"
                           + (f"  \n{s['note']}" if s.get("note") else ""))
                if b.button("Remove", key=f"src-del-{s['id']}", type="tertiary"):
                    svc.remove_source(uid, s["id"])
                    st.rerun()
            with st.popover("Add another source", icon=":material/add_link:"):
                source_form(uid, idea_id, key=f"more-{idea_id}")
            with st.form(f"idea-thesis-{idea_id}", border=False):
                thesis = st.text_area("Thesis and notes", value=idea.get("thesis") or "", height=140,
                                      placeholder="Why it could work, what has to go right, what would make you wrong")
                if st.form_submit_button("Save notes"):
                    svc.update_idea(uid, idea_id, thesis=thesis)
                    st.toast("Saved.")
                    st.rerun()
        with right:
            html_block('<div class="section">Your call</div>')
            types = [None] + list(svc.TYPES)
            t = st.selectbox("Type", types, index=types.index(idea.get("lynch_type")) if idea.get("lynch_type") in types else 0,
                             format_func=lambda x: "Not set" if x is None else svc.TYPES[x], key=f"idea-type-{idea_id}",
                             help="Peter Lynch's categories: how the company is likely to make you money.")
            if t != idea.get("lynch_type"):
                svc.update_idea(uid, idea_id, lynch_type=t)
                st.rerun()
            stats = list(svc.STATUSES)
            stt = st.selectbox("Status", stats, index=stats.index(idea.get("status") or "new"),
                               format_func=svc.STATUSES.get, key=f"idea-status-{idea_id}")
            if stt != (idea.get("status") or "new"):
                svc.update_idea(uid, idea_id, status=stt)
                st.rerun()
            if idea.get("decision"):
                since = (m["price"] / idea["decision_price"] - 1) if idea.get("decision_price") and m.get("price") else None
                st.markdown(f"**{idea['decision']}** on {idea['decision_date']:%d %b %Y}"
                            + (f" at {idea['decision_price']:,.2f}" if idea.get("decision_price") else "")
                            + (f", **{since:+.1%}** since" if since is not None else ""))
            d1, d2 = st.columns([1.2, 1], vertical_alignment="bottom")
            choice = d1.selectbox("Decision", svc.DECISIONS, index=None, placeholder="Record a decision",
                                  key=f"idea-dec-{idea_id}")
            if d2.button("Record", key=f"idea-dec-go-{idea_id}", disabled=not choice, width="stretch",
                         help="Saves today's price with it, so you can see later how the call worked out."):
                svc.set_decision(uid, idea_id, choice, m.get("price"))
                journal.add(uid, idea["market"], idea["ticker"], date.today(),
                            {"BUY": "buy", "SELL": "sell"}.get(choice, "update"),
                            f"{choice}: {idea.get('name') or idea['ticker']}",
                            (f"Price {m['price']:,.2f} {m.get('currency') or ''}. " if m.get("price") else "")
                            + "From Stock ideas.", None, "idea", None)
                st.rerun()
            if idea["market"] != "X":
                from services import watch
                if not watch.is_watching(uid, idea["market"], idea["ticker"]):
                    if st.button("Add to watchlist", key=f"idea-watch-{idea_id}", icon=":material/visibility:"):
                        comp, err = watch.add(user, idea["market"], idea["ticker"])
                        st.warning(err) if err else st.toast("Added to your watchlist.")
            if st.button("Delete idea", key=f"idea-del-{idea_id}", type="tertiary", icon=":material/delete:"):
                svc.remove_idea(uid, idea_id)
                st.rerun()


def source_form(uid: int, idea_id: int | None, key: str, company_box: bool = False,
                letter_id: int | None = None) -> None:
    """One idea (company box) or one more source for an existing idea."""
    letters = svc.list_letters(uid)
    with st.form(f"src-{key}", clear_on_submit=True, border=False):
        company = st.text_input("Company", placeholder="Ticker, Yahoo symbol (COMPERIA.WA, 7012.T) or name") if company_box else ""
        c1, c2 = st.columns(2)
        kind = c1.selectbox("Source", list(svc.SOURCE_KINDS), format_func=svc.SOURCE_KINDS.get)
        author = c2.text_input("Who", placeholder="@handle, newsletter or fund")
        url = st.text_input("Link", placeholder="https://")
        letter = None
        if letters:
            labels = {None: "Not from a letter"} | {l["id"]: f"{l['fund']} {l.get('period') or ''}".strip() for l in letters}
            letter = st.selectbox("Quarterly letter", list(labels), format_func=labels.get,
                                  index=list(labels).index(letter_id) if letter_id in labels else 0)
        c3, c4 = st.columns([2, 1])
        note = c3.text_area("What they said", height=90, placeholder="The pitch in a line or two")
        when = c4.date_input("Date", value=date.today(), max_value=date.today())
        lynch = c4.selectbox("Type", [None] + list(svc.TYPES), format_func=lambda x: "Not set" if x is None else svc.TYPES[x]) \
            if company_box else None
        if st.form_submit_button("Add idea" if company_box else "Add source", type="primary"):
            if letter and kind != "letter":
                kind = "letter"
            if letter and not author:
                author = next((l["fund"] for l in letters if l["id"] == letter), "")
            source = dict(kind=kind, author=author, url=url, note=note, letter_id=letter, source_date=when)
            if company_box:
                if not company.strip():
                    st.warning("Enter a ticker or company name.")
                    return
                with st.spinner("Finding the company"):
                    found = svc.resolve(company)
                if not found:
                    st.warning(f"Couldn't find {company}. Try its Yahoo Finance symbol, e.g. COMPERIA.WA or 7012.T.")
                    return
                _, err = svc.add_idea(uid, found[0], found[1], found[2], thesis="", lynch_type=lynch, source=source)
                st.warning(err) if err else st.toast(f"Added {found[2]}.")
            else:
                svc.add_source(uid, idea_id, **source)
                st.rerun()


def extract_flow(user: dict, key: str, letter: dict | None = None) -> None:
    """Paste a post / thread / letter (or upload the letter's PDF): AI lists the companies it pitches; tick and add."""
    uid = user["id"]
    k = f"ex-{key}"
    if letter:
        kind, author, url = "letter", letter["fund"], letter.get("url") or ""
    else:
        c1, c2 = st.columns(2)
        kind = c1.selectbox("Source", list(svc.SOURCE_KINDS), format_func=svc.SOURCE_KINDS.get, key=f"{k}-kind")
        author = c2.text_input("Who", placeholder="@handle, newsletter or fund", key=f"{k}-who")
        url = st.text_input("Link", placeholder="https://", key=f"{k}-url")
    text = st.text_area("Paste the post, thread or letter", height=160, key=f"{k}-text",
                        placeholder="Paste the text here. For a letter you can upload the PDF instead.")
    up = st.file_uploader("Or the letter's PDF", type=["pdf"], key=f"{k}-pdf")
    if up is not None:
        from services import highlights
        try:
            doc = highlights.read_pdf(up.getvalue())
            text = "\n\n".join(p["text"] for p in doc["passages"])
            st.caption(f"Read {doc['pages']} pages.")
        except highlights.DocumentProblem as exc:
            st.warning(str(exc))
    if st.button("Find the companies it pitches", key=f"{k}-go", icon=":material/auto_awesome:",
                 disabled=len((text or "").strip()) < 40,
                 help="One AI request. The text is only used to list companies; it isn't stored."):
        from services.summarize import MemberLimit
        label = f"{svc.SOURCE_KINDS.get(kind, 'a source')}" + (f" by {author}" if author else "")
        try:
            with st.spinner("Reading"):
                found = svc.extract_mentions(text, label)
        except MemberLimit as exc:
            st.warning(str(exc))
            return
        st.session_state[f"{k}-found"] = found
        if not found:
            st.info("No companies found, or AI isn't available just now. You can add ideas one by one instead.")
    found = st.session_state.get(f"{k}-found") or []
    if not found:
        return
    st.caption("Tick the ones to keep. Fix a ticker if it's wrong: a Yahoo symbol like COMPERIA.WA always works.")
    picks = []
    for i, f in enumerate(found):
        a, b, c = st.columns([0.4, 1.3, 3], vertical_alignment="center")
        keep = a.checkbox("Keep", value=True, key=f"{k}-keep-{i}", label_visibility="collapsed")
        sym = b.text_input("Ticker", value=f["ticker"] or f["name"], key=f"{k}-sym-{i}", label_visibility="collapsed")
        c.markdown(f"**{esc(f['name'])}**" + (f" ({esc(f['exchange'])})" if f.get("exchange") else "")
                   + (f"  \n{esc(f['thesis'])}" if f.get("thesis") else ""))
        if keep:
            picks.append((sym, f))
    if st.button(f"Add {len(picks)} idea{'s' if len(picks) != 1 else ''}", type="primary", key=f"{k}-add", disabled=not picks):
        added, missed = 0, []
        with st.spinner("Finding the companies"):
            for sym, f in picks:
                hit = svc.resolve(sym, f["name"])
                if not hit:
                    missed.append(f["name"])
                    continue
                _, err = svc.add_idea(uid, hit[0], hit[1], hit[2], source=dict(
                    kind=kind, author=author, url=url, note=f.get("thesis") or "",
                    letter_id=(letter or {}).get("id"), source_date=(letter or {}).get("published") or date.today()))
                if err:
                    st.warning(err)
                    break
                added += 1
        st.session_state.pop(f"{k}-found", None)
        st.toast(f"Added {added} idea{'s' if added != 1 else ''}.")
        if missed:
            st.warning("Couldn't find: " + ", ".join(missed) + ". Add them one by one with their Yahoo symbol.")


def letters_tab(user: dict) -> None:
    uid = user["id"]
    with st.expander("Add a quarterly letter", icon=":material/add:", expanded=False):
        with st.form("letter-add", clear_on_submit=True, border=False):
            c1, c2, c3 = st.columns([2, 1, 1])
            fund = c1.text_input("Fund or manager", placeholder="e.g. Greenhaven Road, Laughing Water")
            period = c2.text_input("Quarter", placeholder="Q3 2026")
            published = c3.date_input("Published", value=date.today(), max_value=date.today())
            url = st.text_input("Link to the letter", placeholder="https://")
            notes = st.text_area("Your notes (optional)", height=80)
            if st.form_submit_button("Save letter", type="primary"):
                _, err = svc.add_letter(uid, fund, period, published, url, notes)
                st.warning(err) if err else st.rerun()
    letters = svc.list_letters(uid)
    if not letters:
        st.caption("Keep the quarterly letters you read here. Then pick the companies each one pitches into your ideas, "
                   "linked back to the letter.")
        return
    for l in letters:
        head = f"{l['fund']} {l.get('period') or ''}".strip() + (f" ({l['ideas']} idea{'s' if l['ideas'] != 1 else ''})"
                                                                 if l["ideas"] else "")
        with st.expander(head):
            line = []
            if l.get("published"):
                line.append(f"{l['published']:%d %b %Y}")
            if l.get("url"):
                line.append(f"[Open the letter]({l['url']})")
            if line:
                st.markdown(", ".join(line))
            if l.get("notes"):
                st.markdown(l["notes"])
            ideas_from = [i for i in svc.list_ideas(uid) if any(s.get("letter_id") == l["id"] for s in i["sources"])]
            if ideas_from:
                st.markdown("**Ideas from this letter:** " + ", ".join(f"{esc(i.get('name') or i['ticker'])} ({esc(i['ticker'])})"
                                                                         for i in ideas_from))
            st.caption("Find the companies it pitches: paste the text or upload the PDF.")
            extract_flow(user, key=f"letter-{l['id']}", letter=l)
            if st.button("Delete letter", key=f"letter-del-{l['id']}", type="tertiary",
                         help="Ideas you added from it stay; they just lose the link to the letter."):
                svc.remove_letter(uid, l["id"])
                st.rerun()


def page() -> None:
    user = st.session_state["user"]
    rows = svc.list_ideas(user["id"])
    n_letters = len(svc.list_letters(user["id"]))
    page_header("Stock ideas", f"{len(rows)} idea{'s' if len(rows) != 1 else ''}, {n_letters} letter{'s' if n_letters != 1 else ''}")
    t1, t2, t3 = st.tabs(["Ideas", "Add ideas", f"Quarterly letters ({n_letters})"])
    with t1:
        ideas_tab(user)
    with t2:
        html_block('<div class="section">One idea</div>')
        source_form(user["id"], None, key="new", company_box=True)
        html_block('<div class="section">From a post, thread or letter</div>')
        st.caption("Paste a Substack post, an X thread or a letter: AI lists the companies it pitches, you tick the ones "
                   "to keep, and each is saved with the source.")
        extract_flow(user, key="paste")
    with t3:
        letters_tab(user)
