"""France: regulated information from the AMF's official storage (info-financiere.gouv.fr open API, free).
Companies are identified by ISIN. Switch on with ENABLE_AMF = "true"."""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .kr_dart import tidy_english_name
from .doctext import any_text
from .webhttp import PoliteClient

API = "https://www.info-financiere.gouv.fr/api/explore/v2.0/catalog/datasets"
FEED = f"{API}/flux-amf-new-prod/records"
ISINS = "liste-code-isi"


def _fields(rec: dict) -> dict:
    return (rec.get("record") or {}).get("fields") or rec


class AmfSource(FilingSource):
    market = "FR"
    country = "France"
    regulator = "AMF"
    source_lang = "fr"
    timezone = "Europe/Paris"
    ticker_hint = "ISIN or company name, e.g. FR0000121014 or LVMH"
    news_local = {"hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    attribution = "Source: AMF, info-financiere.gouv.fr (open data)"
    backfill_days = 180
    incremental_days = 3

    def __init__(self):
        self.http = PoliteClient("amf", pause=0.3)
        self._listing, self._at, self._lock = None, 0.0, threading.Lock()

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_AMF", "false")).lower() in {"1", "true", "yes"}

    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._listing is None or time.time() - self._at > 86400:
                rows = []
                try:
                    rows = self.http.get(f"{API}/{ISINS}/exports/json").json()
                except Exception:
                    for offset in range(0, 10000, 100):
                        page = self.http.get(f"{API}/{ISINS}/records", params={"limit": 100, "offset": offset}).json()
                        recs = page.get("records") or page.get("results") or []
                        rows += [_fields(r) for r in recs]
                        if len(recs) < 100:
                            break
                out, seen = [], set()
                for r in rows:
                    isin = (r.get("identificationsociete_iso_cd_isi") or "").strip().upper()
                    name = " ".join(str(r.get("identificationsociete_iso_nom_soc") or "").split())
                    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin) and isin not in seen:
                        seen.add(isin)
                        out.append(Company(self.market, isin, isin, name, tidy_english_name(name) if name.isupper() else name))
                self._listing, self._at = out, time.time()
            return self._listing

    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper()
        return t if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", t) else None

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        return next((c for c in self.all_listed() if c.ticker == t), None) if t else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        hits = [c for c in self.all_listed() if q == c.ticker.lower() or q in c.name_en.lower()]
        hits.sort(key=lambda c: (not c.name_en.lower().startswith(q), len(c.name_en)))
        return hits[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("info-financiere", "https://www.info-financiere.gouv.fr/")]

    def _records(self, where: str, limit: int = 100) -> list[dict]:
        page = self.http.get(FEED, params={"where": where, "order_by": "uin_dat_amf desc", "limit": limit}).json()
        return [_fields(r) for r in (page.get("records") or page.get("results") or [])]

    def _filing(self, r: dict, company: Company) -> Filing | None:
        try:
            when = datetime.fromisoformat(str(r.get("informationdeposee_inf_dat_emt") or r.get("uin_dat_amf"))).date()
        except ValueError:
            return None
        title = " ".join(str(r.get("informationdeposee_inf_tit_inf") or r.get("type_of_information") or "Regulated information").split())
        subtype = (r.get("subtype_of_information") or "").strip()
        if subtype and subtype.lower() not in title.lower():
            title = f"{title} ({subtype})"
        url = r.get("url_de_recuperation") or ""
        english = title if (r.get("informationdeposee_inf_lng_inf") or "").lower().startswith("anglais") or title.isascii() else ""
        return Filing(uid=f"FR:{r.get('uin_idt_uin')}", market=self.market, ticker=company.ticker,
                      company_name=company.name_en, filed_date=when, title_local=title,
                      filer=r.get("identificationsociete_iso_nom_soc") or "", url=url, title_en=english)

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        where = f'identificationsociete_iso_cd_isi="{company.source_id}" and uin_dat_amf >= date\'{start}\''
        return [f for f in (self._filing(r, company) for r in self._records(where)) if f and start <= f.filed_date <= end]

    def market_feed(self) -> list[dict]:
        out = []
        for r in self._records(f"uin_dat_amf >= date'{self.today() - timedelta(days=1)}'", limit=100):
            isin = (r.get("identificationsociete_iso_cd_isi") or "").upper()
            name = r.get("identificationsociete_iso_nom_soc") or ""
            f = self._filing(r, Company(self.market, isin, isin, name, name))
            if f:
                out.append({"uid": f.uid, "ticker": isin, "company": name, "title_local": f.title_local,
                            "title_en": f.title_en, "url": f.url, "date": f.filed_date})
        return out

    def fetch_document_text(self, uid: str) -> str:
        from core.db import filings, get_engine
        from sqlalchemy import select
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url:
            return ""
        r = self.http.get(url)
        return any_text(r.content, r.headers.get("content-type") or "")
