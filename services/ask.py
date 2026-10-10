"""Answer a question about one filing, using only that filing's text (Gemini or Claude)."""
from __future__ import annotations

from .summarize import _anthropic, _gemini, providers

PROMPT = """Answer the question using only the stock-exchange filing below. Be brief and specific: quote figures,
dates and names exactly as the filing gives them. If the filing doesn't say, answer "The filing doesn't say." and
nothing else. No investment advice.

Company: {company}
Filing: {title}
Original title: {title_local}

Overview:
{summary}

Filing text (English, may be partial):
{body}

Question: {question}"""


def available() -> bool:
    from core.config import get_secret
    return bool(get_secret("GEMINI_API_KEY") or get_secret("ANTHROPIC_API_KEY"))


def answer(question: str, filing: dict) -> str:
    question = " ".join((question or "").split())[:500]
    if not question:
        return "Type a question first."
    prompt = PROMPT.format(company=filing.get("company_name", ""), title=filing.get("title_en", ""),
                           title_local=filing.get("title_local", ""), summary=filing.get("summary_en") or "",
                           body=(filing.get("body_en") or "")[:12000], question=question)
    for provider in providers():
        try:
            result = provider(prompt)
            if result:
                return result.strip()
        except Exception:
            continue
    return "No answer right now. Add a Gemini key in the app secrets, or try again in a minute."


# ---- questions across many filings ------------------------------------------------------------------
import re as _re

STOPWORDS = {"what", "which", "who", "when", "where", "how", "did", "does", "do", "my", "the", "a", "an", "of", "in",
             "on", "for", "to", "and", "or", "about", "any", "are", "is", "was", "were", "this", "that", "these",
             "those", "month", "week", "year", "companies", "company", "announce", "announced", "filings", "filing",
             "with", "from", "have", "has", "had", "there", "their", "them", "me", "show", "list", "tell", "all", "recent"}
TOPICS = {"buyback": ["buyback", "repurchase", "treasury", "own shares"], "dividend": ["dividend", "payout", "distribution"],
          "earnings": ["result", "earning", "profit", "revenue", "sales", "guidance", "ebitda", "loss"],
          "capital": ["offering", "placement", "raise", "issuance", "convertible", "bond", "dilution"],
          "mna": ["merger", "acquisition", "acquire", "takeover", "tender", "disposal", "spin"],
          "insider": ["insider", "director", "executive", "ceo", "bought", "sold"],
          "contract": ["contract", "order", "agreement", "deal"],
          "board": ["appoint", "resign", "board", "auditor", "management"]}

MANY_PROMPT = """You answer questions about stock-exchange filings. Use ONLY the numbered filings below. After each
fact, cite its filing number in square brackets, e.g. [3]. Be concise: a short paragraph or a few bullet points
grouped by company. Give figures and dates exactly as stated. If none of the filings answer the question, say so
plainly. No investment advice.

Question: {question}

Filings:
{items}"""


def _words(text: str) -> list[str]:
    return [w for w in _re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2 and w not in STOPWORDS]


def pick(question: str, rows: list[dict], limit: int = 60) -> list[dict]:
    """The filings most related to the question (words and topics), newest first among equals."""
    words = set(_words(question))
    topics = {t for t, keys in TOPICS.items() if any(k in question.lower() for k in keys)}
    scored = []
    for r in rows:
        text = " ".join(str(r.get(k) or "") for k in ("title_en", "summary_en", "company_name", "ticker")).lower()
        score = sum(2 for w in words if w in text) + (4 if r.get("category") in topics else 0)
        score += sum(1 for t in topics for k in TOPICS[t] if k in text)
        scored.append((score, r["filed_date"], r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top = [r for s, _, r in scored if s > 0][:limit]
    return top or [r for _, _, r in sorted(scored, key=lambda x: x[1], reverse=True)[:limit]]


def ask_filings(question: str, rows: list[dict]) -> tuple[str, list[dict]]:
    """Answer across many filings. Returns the answer and the filings it was given (numbered from 1)."""
    question = " ".join((question or "").split())[:500]
    if not question:
        return "Type a question first.", []
    chosen = pick(question, rows)
    if not chosen:
        return "There are no filings in this period to search.", []
    items = "\n".join(
        f"[{i}] {r['filed_date']} | {r['company_name']} ({r['ticker']}) | {r.get('title_en') or r.get('title_local')}"
        + (f" | {(r.get('summary_en') or '').replace(chr(10), ' ')[:350]}" if r.get("summary_en") else "")
        for i, r in enumerate(chosen, 1))
    prompt = MANY_PROMPT.format(question=question, items=items)
    for provider in providers():
        try:
            result = provider(prompt)
            if result:
                return result.strip(), chosen
        except Exception:
            continue
    return "No answer right now. Add a Gemini key in the app secrets, or try again in a minute.", chosen


# ---- questions about the member's own portfolio ---------------------------------------------------------------------
PORTFOLIO_PROMPT = """You answer questions about the investor's own portfolio, using ONLY the data below. Be concise and
specific: name holdings, give percentages and figures as shown, and cite filings by their number in square brackets,
e.g. [3]. If the data doesn't answer the question, say so. Explain facts; don't recommend buying or selling.

Question: {question}

PORTFOLIO (base currency {base}){amount_note}
{holdings}

RETURNS ON YOUR MONEY: {returns}

UPCOMING DATES:
{events}

YOUR NOTES (journal theses, targets, fair values):
{notes}

RECENT FILINGS FROM HOLDINGS (numbered):
{filings}"""


def portfolio_context(user_id: int, show_amounts: bool) -> tuple[str, list[dict]] | None:
    from datetime import date, timedelta
    from . import fairvalue, journal, portfolio, valuation
    from .digests import _filings, snapshot
    from .events import expected_results, upcoming
    snap = snapshot(user_id)
    if not snap:
        return None
    rows = [r for r in snap["rows"]]
    pairs = [(r["market"], r["ticker"]) for r in rows if not r.get("is_cash")]
    lines = []
    for r in rows:
        if r.get("is_cash"):
            lines.append(f"- Cash {r['currency']}: weight {r['weight'] * 100:.1f}%" + (f", {r['value_base']:,.0f} {snap['base']}" if show_amounts else ""))
            continue
        try:
            q = valuation.quick_view(r["market"], r["ticker"], portfolio.price_history(r["market"], r["ticker"]), r["currency"])
        except Exception:
            q = {}
        extra = ", ".join(f"{label} {valuation.fmt(q, key, kind)}" for label, key, kind in valuation.ROWS
                          if key in ("pe", "growth", "op_margin", "ret1y", "drawdown") and valuation.fmt(q, key, kind) != "–")
        lines.append(f"- {r['name']} ({r['ticker']}, {r['country']}, {r['currency']}): weight {r['weight'] * 100:.1f}%, "
                     f"gain {('%+.1f%%' % (r['gain_pct'] * 100)) if r.get('gain_pct') is not None else 'n/a'}, "
                     f"today {('%+.1f%%' % (r['day'] * 100)) if r.get('day') is not None else 'n/a'}"
                     + (f", value {r['value_base']:,.0f} {snap['base']}" if show_amounts and r.get('value_base') else "")
                     + (f"; {extra}" if extra else ""))
    rets = ", ".join(f"{r['label']} {r['portfolio'] * 100:+.1f}%" for r in snap.get("money_returns") or [] if r.get("portfolio") is not None)
    today = date.today()
    ev = upcoming(pairs, days=60) + [e for e in expected_results(pairs, today, today + timedelta(days=90)) if e.get("estimate")]
    events = "\n".join(f"- {e['event_date']} {e['company_name']}: {e['label']}" for e in sorted(ev, key=lambda e: e["event_date"])[:30]) or "- none known"
    notes = []
    fv = fairvalue.get_all(user_id)
    for m, t in pairs:
        if (m, t) in fv:
            notes.append(f"- {t}: fair value {fv[(m, t)]['value']:,.2f}")
        for e in journal.entries(user_id, m, t)[:3]:
            notes.append(f"- {t} {e['kind']} {e['entry_date']}: {e['title'] or ''} {(e['body'] or '')[:200]}"
                         + (f" (target {e['target_price']:,.2f})" if e.get("target_price") else ""))
    filings = _filings(pairs, since_date=today - timedelta(days=45))[:40]
    flist = "\n".join(f"[{i}] {f['filed_date']} {f['company_name']}: {f['title_en']}"
                      + (f" | {(f.get('summary_en') or '').replace(chr(10), ' ')[:250]}" if f.get("summary_en") else "")
                      for i, f in enumerate(filings, 1)) or "- none"
    text = PORTFOLIO_PROMPT.format(question="{question}", base=snap["base"],
                                   amount_note="" if show_amounts else " (amounts hidden by the investor; use percentages)",
                                   holdings="\n".join(lines), returns=rets or "n/a", events=events,
                                   notes="\n".join(notes) or "- none", filings=flist)
    return text, filings


def ask_portfolio(question: str, user_id: int, show_amounts: bool = True) -> tuple[str, list[dict]]:
    question = " ".join((question or "").split())[:500]
    if not question:
        return "Type a question first.", []
    ctx = portfolio_context(user_id, show_amounts)
    if not ctx:
        return "Add some holdings on the Portfolio page first.", []
    prompt, filings = ctx
    if not providers(private=True):
        return "Portfolio questions are turned off on this site (PRIVATE_AI_PROVIDER is set to none).", filings
    for provider in providers(private=True):
        try:
            result = provider(prompt.replace("{question}", question))
            if result:
                return result.strip(), filings
        except Exception:
            continue
    return "No answer right now. Add a Gemini key in the app secrets, or try again in a minute.", filings
