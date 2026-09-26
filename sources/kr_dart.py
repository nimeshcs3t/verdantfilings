"""South Korea: OpenDART (Financial Supervisory Service). Free API key at https://opendart.fss.or.kr"""
from __future__ import annotations

import io
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from core.config import get_secret

from .base import Company, Filing, FilingSource

API = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
TIMEOUT = 30

_SUFFIX_RE = re.compile(
    r"[\s,\.]*(co\.?,?\s*,?\.?\s*ltd\.?|company\s+limited|corporation|corp\.?|inc\.?|limited|ltd\.?)\s*$", re.I)


class DartError(RuntimeError):
    pass


def tidy_english_name(name: str) -> str:
    """'SAMSUNG ELECTRONICS CO,.LTD' -> 'Samsung Electronics'; keeps short acronyms like SK, LG, KB."""
    name = (name or "").strip()
    for _ in range(2):
        name = _SUFFIX_RE.sub("", name).strip(" ,.")
    if name.isupper():
        name = " ".join(w if len(w) <= 3 else w.capitalize() for w in name.split())
    return name


class DartSource(FilingSource):
    market = "KR"
    country = "South Korea"
    regulator = "DART"
    source_lang = "ko"
    timezone = "Asia/Seoul"
    ticker_hint = "6-character KRX code, e.g. 005930"
    news_local = {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}

    def __init__(self):
        self._corp_map: dict[str, dict] | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    # ---- plumbing -------------------------------------------------------
    def _key(self) -> str | None:
        return get_secret("DART_API_KEY")

    def is_configured(self) -> bool:
        return bool(self._key())

    def _json(self, endpoint: str, **params) -> dict:
        params["crtfc_key"] = self._key()
        r = requests.get(f"{API}/{endpoint}", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _zip(self, endpoint: str, **params) -> zipfile.ZipFile:
        params["crtfc_key"] = self._key()
        r = requests.get(f"{API}/{endpoint}", params=params, timeout=60)
        r.raise_for_status()
        try:
            return zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile:
            # DART answers errors as a small XML/JSON document instead of a zip
            msg = re.search(rb"<message>(.*?)</message>", r.content, re.S)
            raise DartError(msg.group(1).decode("utf-8", "ignore") if msg else "DART returned an unexpected response")

    def _corps(self) -> dict[str, dict]:
        """Listed companies keyed by stock code. Refreshed daily."""
        with self._lock:
            if self._corp_map is None or time.time() - self._loaded_at > 86400:
                zf = self._zip("corpCode.xml")
                root = ET.fromstring(zf.read(zf.namelist()[0]))
                corp_map = {}
                for item in root.iter("list"):
                    stock = (item.findtext("stock_code") or "").strip()
                    if stock:
                        corp_map[stock] = {
                            "corp_code": (item.findtext("corp_code") or "").strip(),
                            "name_local": (item.findtext("corp_name") or "").strip(),
                            "name_en": (item.findtext("corp_eng_name") or "").strip(),
                        }
                self._corp_map, self._loaded_at = corp_map, time.time()
            return self._corp_map

    # ---- companies ------------------------------------------------------
    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper()
        t = re.sub(r"\.(KS|KQ|KRX)$", "", t)
        if len(t) == 7 and t.startswith("A"):
            t = t[1:]
        return t if re.fullmatch(r"[0-9][0-9A-Z]{5}", t) else None

    def _company(self, ticker: str, info: dict) -> Company:
        name_en = tidy_english_name(info["name_en"])
        if not name_en:
            try:
                data = self._json("company.json", corp_code=info["corp_code"])
                name_en = tidy_english_name(data.get("corp_name_eng", ""))
            except Exception:
                name_en = ""
        return Company(self.market, ticker, info["corp_code"], info["name_local"], name_en or info["name_local"])

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        info = self._corps().get(t) if t else None
        return self._company(t, info) if info else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        hits = []
        for stock, info in self._corps().items():
            local, en = info["name_local"].lower(), info["name_en"].lower()
            if q == stock.lower() or q in local or q in en:
                rank = 0 if q in (local, tidy_english_name(info["name_en"]).lower(), stock.lower()) else 1
                hits.append((rank, len(info["name_local"]), stock, info))
        hits.sort(key=lambda h: (h[0], h[1]))
        return [Company(self.market, s, i["corp_code"], i["name_local"], tidy_english_name(i["name_en"]) or i["name_local"])
                for _, _, s, i in hits[:limit]]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("Naver Finance", f"https://finance.naver.com/item/main.naver?code={company.ticker}")]

    # ---- filings --------------------------------------------------------
    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out, page = [], 1
        while True:
            data = self._json("list.json", corp_code=company.source_id, bgn_de=start.strftime("%Y%m%d"),
                              end_de=end.strftime("%Y%m%d"), page_no=page, page_count=100)
            status = data.get("status")
            if status == "013":          # no filings in range
                break
            if status != "000":
                raise DartError(f"DART error {status}: {data.get('message')}")
            for it in data.get("list", []):
                out.append(Filing(
                    uid=f"KR:{it['rcept_no']}",
                    market=self.market,
                    ticker=company.ticker,
                    company_name=company.name_en or it.get("corp_name", ""),
                    filed_date=datetime.strptime(it["rcept_dt"], "%Y%m%d").date(),
                    title_local=re.sub(r"\s+", " ", it.get("report_nm", "")).strip(),
                    filer=it.get("flr_nm", ""),
                    url=VIEWER.format(it["rcept_no"]),
                ))
            if page >= int(data.get("total_page", 1)):
                break
            page += 1
        return out

    def fetch_document_text(self, uid: str) -> str:
        rcept_no = uid.split(":", 1)[1]
        zf = self._zip("document.xml", rcept_no=rcept_no)
        names = sorted(zf.namelist(), key=lambda n: (not n.startswith(f"{rcept_no}."), n))
        parts = []
        for name in names:
            raw = zf.read(name)
            for enc in ("utf-8", "cp949"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode("utf-8", "ignore")
            parts.append(_xml_to_text(text))
        return "\n\n".join(p for p in parts if p)


def _xml_to_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.find_all(["style", "script"]):
        tag.decompose()
    # Keep table rows on one line ("label | value") so figures stay next to their labels.
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th", "te", "tu"])]
        tr.replace_with("\n" + " | ".join(c for c in cells if c) + "\n")
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln and ln != "|")
