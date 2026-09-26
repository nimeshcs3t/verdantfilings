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
