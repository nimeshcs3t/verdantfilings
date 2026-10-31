"""Notes from a document: upload a PDF you highlighted (or pick / paste passages here), let AI write short notes,
check them, and save them to your journal."""
from __future__ import annotations

import hashlib
from datetime import date

import streamlit as st

from core.ui import esc, html_block
from services import highlights as hl, journal

MAX_PICK = 60


def _read(data: bytes) -> dict:
    """Read once per file (kept in this browser session only, never stored)."""
    digest = hashlib.sha1(data).hexdigest()
    cache = st.session_state.setdefault("_doc_cache", {})
    if digest not in cache:
        cache.clear()                                   # keep one document in memory at a time
        cache[digest] = hl.read_pdf(data)
    return cache[digest]


def doc_notes(user: dict, key: str, preset: tuple[str, str] | None = None, filing: dict | None = None) -> None:
    from .journal import _companies
    uid = user["id"]
    k = f"dn-{key}"

    # 1. what it's about
    companies = _companies(uid)
    labels = list(companies)
    if filing:
        preset = (filing["market"], filing["ticker"])
    index = next((i for i, lab in enumerate(labels) if companies[lab] == preset), 0) if preset else 0
    company = st.selectbox("Company", labels, index=index, disabled=bool(preset) and preset in companies.values(),
                           key=f"{k}-co")
    market, ticker = companies[company]

    # which filing (the company's filings of the last year), or another document such as a call transcript
    options: dict[str, dict | None] = {"Another document (not a filing here)": None}
    if market:
        from datetime import timedelta
        from services.pipeline import filings_for
        try:
            recent = filings_for([(market, ticker)], date.today() - timedelta(days=365), limit=150)
        except Exception:
            recent = []
        for r in recent:
            options[f"{r['filed_date']:%d %b %Y}  {(r.get('title_en') or r.get('title_local') or '')[:90]}"] = r
    if filing and not any(o and o.get("uid") == filing.get("uid") for o in options.values()):
        options[f"{filing['filed_date']:%d %b %Y}  {(filing.get('title_en') or filing.get('title_local') or '')[:90]}"] = filing
    picked_label = next((lab for lab, o in options.items() if o and filing and o.get("uid") == filing.get("uid")), None)
    c1, c2 = st.columns([1.6, 1])
    which = c1.selectbox("Which filing", list(options), key=f"{k}-which-{market}-{ticker}",
                         index=list(options).index(picked_label) if picked_label else 0,
                         help="Links the notes to that filing (they show under it). Pick 'Another document' for a "
                              "call transcript, a presentation from the company's website, and so on.")
    filing = options[which]
    guess = hl.guess_type((filing or {}).get("title_en") or (filing or {}).get("title_local") or "")
    kind_key = f"{k}-kind-{(filing or {}).get('uid', 'none')}"          # a fresh guess when the filing changes
    kind = c2.selectbox("Document type", list(hl.NOTE_TYPES), format_func=lambda x: hl.NOTE_TYPES[x][0], key=kind_key,
                        index=list(hl.NOTE_TYPES).index(guess) if guess else None, placeholder="Choose the type",
                        help="Decides the headings the notes use.")
    if filing and filing.get("url"):
        st.link_button("Download this filing's document", filing["url"], icon=":material/download:")

    # 2. the passages
    up = st.file_uploader("Your PDF", type=["pdf"], key=f"{k}-file",
                          help="Highlight in any PDF reader (Acrobat, Edge, Chrome's PDF editor, Preview on Mac, Files or "
                               "Books on iPhone/iPad), save, then upload here. The file is read and then forgotten: only "
                               "the notes you save are kept.")
    doc = None
    if up is not None:
        try:
            with st.spinner("Reading the PDF"):
                doc = _read(up.getvalue())
        except hl.DocumentProblem as exc:
            st.warning(str(exc))
        except Exception:
            st.warning("This PDF couldn't be read. Try saving it again from your PDF reader, or paste the text instead.")

    if up is not None and doc is not None:
        translate_box(up, k)

    found = doc["highlights"] if doc else []
    t_marks, t_pick, t_paste = st.tabs([f"My highlights ({len(found)})", "Pick passages here", "Paste text"])
    chosen: list[dict] = []
    with t_marks:
        if not doc:
            st.caption("Upload a PDF you highlighted to see your highlights here.")
        elif not found:
            st.caption(f"No highlights found in this PDF ({doc['pages']} pages). Highlight and save it in your PDF "
                       "reader first, or use Pick passages here.")
        else:
            st.caption(f"{len(found)} highlights on {len({h['page'] for h in found})} of {doc['pages']} pages. "
                       "Untick any you don't want.")
            for i, h in enumerate(found):
                label = f"p.{h['page']}: {h['text'] or '(comment only)'}" + (f"  ✎ {h['comment']}" if h.get("comment") else "")
                if st.checkbox(label[:400], value=True, key=f"{k}-h-{i}"):
                    chosen.append(h)
    with t_pick:
        if not doc:
            st.caption("Upload a PDF, then tick the paragraphs you want notes on. Useful when you didn't highlight it.")
        else:
            q = st.text_input("Find", placeholder="e.g. margin, guidance, order book", key=f"{k}-find")
            pages = sorted({p["page"] for p in doc["passages"]})
            page = st.selectbox("Page", ["All pages"] + pages, key=f"{k}-page") if len(pages) > 1 else "All pages"
            shown = [(i, p) for i, p in enumerate(doc["passages"])
                     if (page == "All pages" or p["page"] == page) and (not q.strip() or q.lower() in p["text"].lower())]
            st.caption(f"{len(shown)} paragraphs" + (" (first 150 shown; narrow with Find or Page)" if len(shown) > 150 else ""))
            for i, p in shown[:150]:
                if st.checkbox(f"p.{p['page']}: {p['text'][:500]}", key=f"{k}-p-{i}"):
                    chosen.append({"page": p["page"], "text": p["text"], "comment": ""})
    with t_paste:
        pasted = st.text_area("Paste the parts you care about", height=160, key=f"{k}-paste",
                              placeholder="Paste passages from the transcript or report. Separate them with a blank line.")
        for part in [x.strip() for x in pasted.split("\n\n") if x.strip()]:
            chosen.append({"page": None, "text": " ".join(part.split()), "comment": ""})

    if len(chosen) > MAX_PICK:
        st.warning(f"{len(chosen)} passages chosen; only the first {MAX_PICK} are used. Fewer, sharper highlights make "
                   "better notes.")
        chosen = chosen[:MAX_PICK]

    # 3. notes
    focus = st.text_input("Anything to focus on? (optional)", placeholder="e.g. order intake, margins, capex plans",
                          key=f"{k}-focus")
    company_name = company.rsplit(" (", 1)[0] if companies[company][0] else ""
    if chosen and not kind:
        st.caption("Choose the document type above to make notes.")
    if st.button(f"Make notes from {len(chosen)} passage{'s' if len(chosen) != 1 else ''}", type="primary",
                 disabled=not chosen or not kind, key=f"{k}-go", icon=":material/auto_awesome:"):
        from services.summarize import MemberLimit
        try:
            with st.spinner("Writing your notes"):
                notes, by_ai = hl.make_notes(chosen, kind, company_name, focus)
        except MemberLimit as exc:
            st.warning(str(exc))
            return
        st.session_state[f"{k}-notes"] = notes
        st.session_state[f"{k}-items"] = chosen
        st.session_state[f"{k}-by-ai"] = by_ai
        st.session_state[f"{k}-made-for"] = {"kind": kind, "filing": filing}

    notes = st.session_state.get(f"{k}-notes")
    if not notes:
        return
    if not st.session_state.get(f"{k}-by-ai"):
        st.info("AI wasn't available just now, so these are your passages as bullets. You can still edit and save them.")
    html_block('<div class="section">Your notes</div>')
    st.caption("Check them against your highlights before saving; edit anything.")
    st.session_state.setdefault(f"{k}-edit", notes)
    if st.session_state.get(f"{k}-edit-src") != notes:          # fresh notes replace the box's text
        st.session_state[f"{k}-edit"], st.session_state[f"{k}-edit-src"] = notes, notes
    with st.form(f"{k}-save", border=True):
        c1, c2 = st.columns([2, 1])
        made = st.session_state.get(f"{k}-made-for") or {"kind": kind, "filing": filing}
        made_filing = made.get("filing")
        default_title = (f"Notes: {(made_filing.get('title_en') or made_filing.get('title_local') or '')[:150]}"
                         if made_filing else f"{hl.NOTE_TYPES[made['kind'] or 'other'][0].split(' /')[0]} notes, {date.today():%b %Y}")
        title = c1.text_input("Title", value=default_title)
        day = c2.date_input("Date", value=date.today(), max_value=date.today())
        body = st.text_area("Notes", key=f"{k}-edit", height=320)
        c3, c4 = st.columns([2, 1])
        tags = c3.text_input("Tags", placeholder="e.g. Q3, concall, margins")
        keep_quotes = c4.checkbox("Keep my highlights under the notes", value=True)
        if st.form_submit_button("Save to journal", type="primary"):
            market, ticker = companies[company]
            items = st.session_state.get(f"{k}-items") or []
            err = journal.add(uid, market, ticker, day, "notes", title, hl.body_with_quotes(body, items, keep_quotes),
                              None, tags, None, filing_uid=(made_filing or {}).get("uid"))
            if err:
                st.warning(err)
            else:
                for s in (f"{k}-notes", f"{k}-items", f"{k}-edit", f"{k}-edit-src", f"{k}-made-for"):
                    st.session_state.pop(s, None)
                st.session_state.pop("_doc_cache", None)
                st.session_state.pop("doc_notes_filing", None)
                st.toast("Saved to your journal.")
                st.rerun()
    with st.expander("Preview"):
        st.markdown(st.session_state.get(f"{k}-edit") or notes)


def translate_box(up, k: str) -> None:
    """Translate the whole uploaded PDF into English, keeping its layout, and offer it for download."""
    from services import pdf_translate
    digest = hashlib.sha1(up.getvalue()).hexdigest()
    done = st.session_state.get(f"{k}-tr")
    c1, c2 = st.columns([1.3, 2], vertical_alignment="center")
    if done and done[0] == digest:
        c1.download_button("Download the English PDF", done[1], file_name=f"{up.name.rsplit('.', 1)[0]} (English).pdf",
                           mime="application/pdf", icon=":material/download:", type="primary", key=f"{k}-tr-dl")
        c2.caption(f"Translated {done[2]['blocks']} text blocks on {done[2]['pages']} pages. You can highlight the "
                   "English copy and upload it here for notes.")
        return
    if c1.button("Translate the whole PDF to English", icon=":material/translate:", key=f"{k}-tr-go",
                 help="Keeps the layout: slides, tables and pictures stay in place, the text is replaced with English. "
                      "Free machine translation (no AI). Up to 80 pages."):
        bar = st.progress(0.0, text="Translating")
        try:
            data, stats = pdf_translate.translate_pdf(up.getvalue(), "en",
                                                      progress=lambda f: bar.progress(min(f, 1.0), text="Translating"))
        except pdf_translate.TranslateProblem as exc:
            bar.empty()
            st.warning(str(exc))
            return
        except Exception:
            bar.empty()
            st.warning("This PDF couldn't be translated. Try again, or save a shorter part of it.")
            return
        bar.empty()
        st.session_state[f"{k}-tr"] = (digest, data, stats)
        st.rerun()
    else:
        c2.caption("Works best for presentations and reports. Scanned pages can't be translated.")
