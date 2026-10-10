"""Free machine translation (Google via deep-translator) with a DB cache and a glossary of common DART titles."""
from __future__ import annotations

import hashlib
import re
import time

import requests
from deep_translator import GoogleTranslator
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from core.config import get_secret
from core.db import get_engine, translations

LANG_NAMES = {"ko": "Korean", "ja": "Japanese", "pl": "Polish", "iw": "Hebrew", "he": "Hebrew", "zh": "Chinese",
              "zh-TW": "Traditional Chinese", "fr": "French", "no": "Norwegian", "sv": "Swedish", "da": "Danish",
              "fi": "Finnish"}
MYMEMORY_CODES = {"iw": "he"}
_google_paused_until = 0.0     # Google refuses shared cloud servers; stop asking for a while after a refusal

# Keys have spaces removed. Exact matches only; everything else goes to machine translation.
GLOSSARY = {
    "사업보고서": "Annual report",
    "반기보고서": "Half-year report",
    "분기보고서": "Quarterly report",
    "감사보고서제출": "Audit report submitted",
    "주요사항보고서": "Material event report",
    "임원ㆍ주요주주특정증권등소유상황보고서": "Insider ownership report (officers and major shareholders)",
    "주식등의대량보유상황보고서(일반)": "Large shareholding report, 5% rule (general)",
    "주식등의대량보유상황보고서(약식)": "Large shareholding report, 5% rule (short form)",
    "최대주주등소유주식변동신고서": "Change in largest shareholder's holdings",
    "최대주주변경": "Change of largest shareholder",
    "기업설명회(IR)개최(안내공시)": "Investor relations event notice",
    "연결재무제표기준영업(잠정)실적(공정공시)": "Preliminary consolidated results (fair disclosure)",
    "영업(잠정)실적(공정공시)": "Preliminary results (fair disclosure)",
    "매출액또는손익구조30%(대규모법인은15%)이상변경": "Revenue or profit changed 30% or more (15% for large companies)",
    "연결재무제표기준매출액또는손익구조30%(대규모법인은15%)이상변경":
        "Consolidated revenue or profit changed 30% or more (15% for large companies)",
    "현금ㆍ현물배당결정": "Cash or in-kind dividend decision",
    "주식소각결정": "Share cancellation decision",
    "자기주식취득결정": "Share buyback decision",
    "자기주식처분결정": "Treasury share disposal decision",
    "자기주식취득신탁계약체결결정": "Buyback trust agreement decision",
    "자기주식취득신탁계약해지결정": "Buyback trust termination decision",
    "자기주식취득결과보고서": "Share buyback results report",
    "단일판매ㆍ공급계약체결": "Sales or supply contract signed",
    "단일판매ㆍ공급계약해지": "Sales or supply contract terminated",
    "유상증자결정": "Rights offering decision",
    "무상증자결정": "Bonus issue decision",
    "유무상증자결정": "Rights offering and bonus issue decision",
    "전환사채권발행결정": "Convertible bond issuance decision",
    "신주인수권부사채권발행결정": "Bond with warrants issuance decision",
    "교환사채권발행결정": "Exchangeable bond issuance decision",
    "전환청구권행사": "Conversion rights exercised",
    "타법인주식및출자증권취득결정": "Decision to acquire shares in another company",
    "타법인주식및출자증권처분결정": "Decision to sell shares in another company",
    "유형자산취득결정": "Tangible asset acquisition decision",
    "유형자산처분결정": "Tangible asset disposal decision",
    "신규시설투자등": "New facility investment",
    "회사합병결정": "Merger decision",
    "회사분할결정": "Company split decision",
    "영업양수결정": "Business acquisition decision",
    "영업양도결정": "Business transfer decision",
    "타인에대한채무보증결정": "Debt guarantee for another party",
    "소송등의제기ㆍ신청": "Lawsuit filed",
    "주주총회소집결의": "Board resolution to call shareholders' meeting",
    "주주총회소집공고": "Notice of shareholders' meeting",
    "정기주주총회결과": "Annual general meeting results",
    "의결권대리행사권유참고서류": "Proxy solicitation document",
    "기업가치제고계획(자율공시)": "Corporate value-up plan (voluntary)",
    "기타경영사항(자율공시)": "Other management matters (voluntary)",
    "투자판단관련주요경영사항": "Material information for investors",
    "자회사의주요경영사항": "Subsidiary's material matter",
    "결산실적공시예고": "Earnings release date notice",
    "증권신고서(지분증권)": "Registration statement (equity)",
    "증권신고서(채무증권)": "Registration statement (debt)",
    "투자설명서": "Prospectus",
    "증권발행실적보고서": "Securities issuance results report",
    "합병등종료보고서": "Merger completion report",
}
PREFIXES = {
    "[기재정정]": "[Amended] ",
    "[첨부정정]": "[Attachment amended] ",
    "[첨부추가]": "[Attachment added] ",
    "[변경등록]": "[Registration changed] ",
    "[연장결정]": "[Extension] ",
    "[발행조건확정]": "[Terms finalized] ",
    "[정정명령부과]": "[Correction ordered] ",
    "[정정제출요구]": "[Correction requested] ",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("·", "ㆍ").replace("・", "ㆍ"))


def _cache_key(text: str, src: str) -> str:
    return hashlib.sha1(f"{src}|{text}".encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    with get_engine().connect() as conn:
        return conn.execute(select(translations.c.text_en).where(translations.c.key == key)).scalar()


def _cache_put(key: str, text_en: str) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(translations).values(key=key, text_en=text_en))
    except IntegrityError:
        pass


def _count(key: str) -> None:
    try:
        from core.usage import count
        count(key)
    except Exception:
        pass


def _google(text: str, src: str) -> str | None:
    global _google_paused_until
    if time.time() < _google_paused_until:
        return None
    try:
        out = GoogleTranslator(source=src or "auto", target="en").translate(text) or None
        if out:
            _count("google")
        return out
    except Exception as exc:
        if "TooManyRequests" in type(exc).__name__ or "too many requests" in str(exc).lower():
            _google_paused_until = time.time() + 1800
            _count("g-refuse")
        return None


def _llm(text: str, src: str) -> str | None:
    """Gemini (free tier) or Claude, if a key is set. Keeps one output line per input line."""
    from .summarize import _anthropic, _gemini, providers
    language = LANG_NAMES.get(src, "the original language")
    prompt = (f"Translate this {language} text from a company's stock-exchange filing into clear English. "
              "Keep the line breaks: return exactly one line of English for each line of input, in the same "
              "order. Return only the translation, with no notes.\n\n" + text)
    for provider in providers():
        try:
            result = provider(prompt)
            if result:
                return result.strip()
        except Exception:
            continue
    return None


def _mymemory(text: str, src: str) -> str | None:
    """Free MyMemory service, no key. Up to 500 characters per request, so only for short text."""
    if src in ("auto", "", None) or len(text) > 480 or "\n" in text:
        return None     # one line at a time, so each title keeps its own translation
    params = {"q": text, "langpair": f"{MYMEMORY_CODES.get(src, src)}|en"}
    email = get_secret("TRANSLATE_CONTACT_EMAIL") or get_secret("SEC_CONTACT_EMAIL")
    if email:
        params["de"] = email       # raises the free daily allowance
    try:
        data = requests.get("https://api.mymemory.translated.net/get", params=params, timeout=20).json()
        english = (data.get("responseData") or {}).get("translatedText")
        if data.get("responseStatus") in (200, "200") and english and "MYMEMORY WARNING" not in english:
            _count("mymemory")
            return english
    except Exception:
        pass
    return None


def _machine(text: str, src: str) -> str | None:
    for translator in (_google, _llm, _mymemory):
        result = translator(text, src)
        if result and result.strip() and result.strip() != text.strip():
            return result
    return None


def _chunks(text: str, size: int = 4500):
    buf = ""
    for line in text.splitlines():
        while len(line) > size:
            if buf:
                yield buf
                buf = ""
            yield line[:size]
            line = line[size:]
        if len(buf) + len(line) + 1 > size:
            yield buf
            buf = ""
        buf = f"{buf}\n{line}" if buf else line
    if buf:
        yield buf


def translate_text(text: str, src: str = "auto") -> str:
    if not text or not text.strip() or src == "en":
        return text or ""
    out = []
    for chunk in _chunks(text):
        out.append(_machine(chunk, src) or chunk)
        time.sleep(0.2)
    return "\n".join(out)


def _glossary_title(title: str, src: str) -> str | None:
    rest, prefix = title.strip(), ""
    while True:
        m = re.match(r"^\[[^\]]+\]", rest)
        if not (m and m.group(0).replace(" ", "") in PREFIXES):
            break
        prefix += PREFIXES[m.group(0).replace(" ", "")]
        rest = rest[m.end():].strip()
    n = _norm(rest)
    if n in GLOSSARY:
        return prefix + GLOSSARY[n]
    m = re.match(r"^(.+?)\((.+)\)$", n)
    if m and m.group(1) in GLOSSARY and m.group(2) in GLOSSARY:
        return f"{prefix}{GLOSSARY[m.group(1)]} ({GLOSSARY[m.group(2)]})"
    if prefix:
        translated = _machine(rest, src)
        return prefix + translated if translated else None
    return None


def translate_title(title: str, src: str = "ko") -> str:
    if not title or src == "en":
        return title
    key = _cache_key(title, src)
    cached = _cache_get(key)
    if cached:
        return cached
    english = _glossary_title(title, src) or _machine(title, src)
    if english:
        _cache_put(key, english)
        return english
    return title


def translate_lines(lines: list[str], src: str) -> list[str]:
    """Translate short lines (headlines) with caching; one request for all uncached lines."""
    results: list[str | None] = []
    missing = []
    for i, line in enumerate(lines):
        cached = _cache_get(_cache_key(line, src))
        results.append(cached)
        if cached is None:
            missing.append(i)
    if missing:
        joined = _machine("\n".join(lines[i] for i in missing), src) if len(missing) > 1 else None
        parts = [p for p in joined.split("\n") if p.strip()] if joined else []
        if len(parts) != len(missing):
            parts = [_machine(lines[i], src) for i in missing]
        for i, en in zip(missing, parts):
            if en and en.strip():
                results[i] = en.strip()
                _cache_put(_cache_key(lines[i], src), results[i])   # only real translations are saved
    return [r or lines[i] for i, r in enumerate(results)]


ENGLISH_WORDS = re.compile(r"\b(the|of|and|for|to|in|on|with|share|shares|report|results|annual|quarterly|interim|notice|"
                           r"update|announces?|buy-?back|meeting|financial|transaction|own|statement|board|director|"
                           r"agreement|contract|offering|dividend|acquisition|disposal|holding|holdings|presentation|"
                           r"company|change|new|release|half-year|year|period|information|voting|rights)\b", re.I)


def looks_english(text: str) -> bool:
    """True for titles that are already English (plain letters and common English filing words)."""
    t = (text or "").strip()
    return bool(t) and t.isascii() and bool(ENGLISH_WORDS.search(t))


def mostly_english(text: str) -> bool:
    """True for document text that is already English (so it needn't be translated)."""
    sample = (text or "")[:2000]
    letters = [c for c in sample if c.isalpha()]
    if len(letters) < 40:
        return False
    ascii_share = sum(c.isascii() for c in letters) / len(letters)
    return ascii_share > 0.97 and len(ENGLISH_WORDS.findall(sample)) >= 5


def glossary_or_cached(title: str, src: str) -> str | None:
    """A title's English from the glossary or earlier translations, without calling a translator."""
    if not title or src == "en":
        return title
    cached = _cache_get(_cache_key(title, src))
    if cached:
        return cached
    english = _glossary_title(title, src) if src == "ko" else None
    if english:
        _cache_put(_cache_key(title, src), english)
    return english


def translate_keyword(word: str, lang: str) -> str | None:
    """An English search keyword in another language (e.g. "buyback" -> "자기주식"), cached."""
    global _google_paused_until
    if not word or lang in ("en", "auto", "", None):
        return None
    key = _cache_key(word.lower(), f"to-{lang}")
    cached = _cache_get(key)
    if cached:
        return cached
    out = None
    if time.time() >= _google_paused_until:
        try:
            out = GoogleTranslator(source="en", target=lang).translate(word)
        except Exception as exc:
            if "toomanyrequests" in type(exc).__name__.lower() or "too many requests" in str(exc).lower():
                _google_paused_until = time.time() + 1800
    if not out:
        from .summarize import _anthropic, _gemini, providers
        language = LANG_NAMES.get(lang, lang)
        prompt = (f"Give the {language} word or short phrase that companies use in stock-exchange filing titles for "
                  f"this English term: \"{word}\". Reply with only the {language} term.")
        for provider in providers():
            try:
                out = provider(prompt)
                if out:
                    break
            except Exception:
                continue
    if not out:
        try:
            data = requests.get("https://api.mymemory.translated.net/get", timeout=20,
                                params={"q": word, "langpair": f"en|{MYMEMORY_CODES.get(lang, lang)}"}).json()
            if data.get("responseStatus") in (200, "200"):
                out = (data.get("responseData") or {}).get("translatedText")
        except Exception:
            out = None
    out = (out or "").strip().strip('"').splitlines()[0].strip() if out else ""
    if out and out.lower() != word.lower():
        _cache_put(key, out)
        return out
    return None
