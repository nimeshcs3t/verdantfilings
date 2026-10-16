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


def _count(key: str) -> None:
    try:
        from core.usage import count
        count(key)
    except Exception:
        pass


GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
_gemini_model: list[str] = []          # the model that last worked, remembered for this run


def _discover_models(key: str) -> list[str]:
    """Ask Google which Flash text models this key can use, newest first."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": key},
                         params={"pageSize": 200}, timeout=30)
        r.raise_for_status()
        names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    except Exception:
        return []
    skip = ("preview", "image", "tts", "live", "audio", "embedding", "thinking", "exp", "-latest")
    flash = [n for n in names if "flash" in n and not any(w in n for w in skip)]

    def version(n: str):
        m = re.search(r"gemini-(\d+)(?:\.(\d+))?", n)
        return (int(m.group(1)), int(m.group(2) or 0), "lite" not in n) if m else (0, 0, False)
    return sorted(flash, key=version, reverse=True)


def gemini_request(body: dict, timeout: int = 60) -> dict | None:
    """POST to Gemini generateContent, trying the configured model, then current Flash models, then whatever the
    key can use. Returns the JSON reply, or None without a key. Raises on other errors (e.g. rate limits)."""
    key = get_secret("GEMINI_API_KEY")
    if not key:
        return None
    configured = get_secret("GEMINI_MODEL")
    tried, candidates = set(), ([*_gemini_model] + ([configured] if configured else []) + GEMINI_MODELS)
    last_error = None
    for attempt in range(2):
        for model in candidates:
            if not model or model in tried:
                continue
            tried.add(model)
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body, timeout=timeout)
            if r.status_code in (404, 400) and ("not found" in r.text.lower() or "no longer available" in r.text.lower()
                                                or r.status_code == 404):
                last_error = f"{model}: {r.status_code}"
                continue
            if r.status_code == 429:
                _count("gem-limit")
            r.raise_for_status()
            _count("gemini")
            if not _gemini_model or _gemini_model[0] != model:
                _gemini_model[:] = [model]
            return r.json()
        if attempt == 0:
            candidates = _discover_models(key)
    raise RuntimeError(f"no Gemini model available for this key ({last_error})")


def _gemini(prompt: str) -> str | None:
    data = gemini_request({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}})
    if data is None:
        return None
    return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()


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


def providers(private: bool = False) -> tuple:
    """AI services to try, in order. AI_PROVIDER picks the first choice ("gemini" or "anthropic"). For prompts that
    contain a member's own portfolio, PRIVATE_AI_PROVIDER can choose differently ("anthropic", "gemini", or "none" to
    never send portfolios to AI). Note: Google may use prompts sent with a free-tier Gemini key to improve its products."""
    from core.config import get_secret
    choice = str(get_secret("PRIVATE_AI_PROVIDER" if private and get_secret("PRIVATE_AI_PROVIDER") else "AI_PROVIDER",
                            "gemini")).strip().lower()
    if choice == "none":
        return ()
    if choice in {"anthropic-only", "claude-only"}:
        return (_anthropic,)
    if choice in {"gemini-only"}:
        return (_gemini,)
    return (_anthropic, _gemini) if choice in {"anthropic", "claude"} else (_gemini, _anthropic)


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
        for provider in providers():
            try:
                result = provider(prompt)
                if result:
                    return result
            except Exception:
                continue
    return _extractive(text_en) or "No overview could be generated for this filing. Open the original for details."
