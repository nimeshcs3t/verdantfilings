"""Short English overview of a filing. Uses an LLM if a key is configured, otherwise a rule-based extract."""
from __future__ import annotations

import re

import requests

from core.config import get_secret

PROMPT = """You write short English briefs of stock-exchange filings for investors.
Use only facts stated in the document. Write 3 to 5 bullet points, each starting with "- ".
Start with what happened, then the key figures (amounts with currency, share counts, percentages, dates)
and the parties involved. Convert local units correctly (억원 = 100 million KRW, 조원 = 1 trillion KRW,
百万円 = 1 million JPY, 億円 = 100 million JPY).
No preamble, no investment advice, no speculation.

Company: {company}
Filing: {title}

Document (may be truncated):
{doc}"""

KEYWORDS = ("decision", "contract", "dividend", "acqui", "dispos", "share", "won", "krw", "revenue", "sales",
            "operating profit", "net income", "merger", "issu", "price", "amount", "ratio", "period", "purpose",
            "counterparty", "buyback", "treasury")


def _gemini(prompt: str) -> str | None:
    key = get_secret("GEMINI_API_KEY")
    if not key:
        return None
    model = get_secret("GEMINI_MODEL", "gemini-2.5-flash")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}},
        timeout=60)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def _anthropic(prompt: str) -> str | None:
    key = get_secret("ANTHROPIC_API_KEY")
    if not key:
        return None
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": get_secret("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"), "max_tokens": 600,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()


def _extractive(text_en: str) -> str:
    candidates = re.split(r"(?<=[.!?])\s+|\n+", text_en or "")
    scored = []
    for i, s in enumerate(candidates):
        s = s.strip(" |")
        if not 20 <= len(s) <= 260:
            continue
        low = s.lower()
        score = min(len(re.findall(r"\d", s)), 6) + 2 * sum(k in low for k in KEYWORDS)
        if score >= 3:
            scored.append((score, i, s))
    seen, picked = set(), []
    for _, i, s in sorted(scored, reverse=True):
        if s.lower() not in seen:
            seen.add(s.lower())
            picked.append((i, s))
        if len(picked) == 5:
            break
    return "\n".join(f"- {s}" for _, s in sorted(picked))


def summarize(text_local: str, text_en: str, title_en: str, company: str) -> str:
    doc = (text_local or text_en or "")[:15000]
    if doc:
        prompt = PROMPT.format(company=company, title=title_en, doc=doc)
        for provider in (_gemini, _anthropic):
            try:
                result = provider(prompt)
                if result:
                    return result
            except Exception:
                continue
    return _extractive(text_en) or "No overview could be generated for this filing. Open the original for details."
