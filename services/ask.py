"""Answer a question about one filing, using only that filing's text (Gemini or Claude)."""
from __future__ import annotations

from .summarize import _anthropic, _gemini

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
    for provider in (_gemini, _anthropic):
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
    for provider in (_gemini, _anthropic):
        try:
            result = provider(prompt)
            if result:
                return result.strip(), chosen
        except Exception:
            continue
    return "No answer right now. Add a Gemini key in the app secrets, or try again in a minute.", chosen
