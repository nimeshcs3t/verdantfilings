"""Netherlands, Belgium, Portugal, Ireland and Italy: company press releases (regulated news) from Euronext's
website (live.euronext.com), the same list its company pages show. Companies are found by ticker (Yahoo Finance)
and matched to Euronext by ISIN. Unofficial, for personal use. Switch on with ENABLE_EURONEXT = "true".

How it reads Euronext (from its public pages):
  instrument search  /en/instrumentSearch/searchJSON?q=<ticker or name>  ->  [{"value": "<ISIN>-<MIC>", ...}]
  product page       /en/product/equities/<ISIN>-<MIC>                    ->  link to company-press-release/<id>
  press releases     /en/listview/company-press-release/<id>?page=<n>    ->  table: Released, Company, Title, ..."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import any_text, html_text
from .webhttp import PoliteClient

log = logging.getLogger(__name__)
BASE = "https://live.euronext.com"


def _date(text: str) -> date | None:
    t = " ".join((text or "").split())
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", t)
    if m:
        try:
            return datetime.strptime(f"{m[1]} {m[2][:3].title()} {m[3]}", "%d %b %Y").date()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def parse_press_list(html: str) -> list[dict]:
    """Rows of a press-release list page: {date, title, topic, nid, href}. Tolerant of small layout changes."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.select("table tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        texts = [" ".join(c.get_text(" ").split()) for c in cells]
        when = next((d for d in (_date(t) for t in texts) if d), None)
        if not when:
            continue
        # the title is the longest cell that isn't the date
        title_i = max(range(len(texts)), key=lambda i: 0 if _date(texts[i]) else len(texts[i]))
        title = texts[title_i]
        if not title:
            continue
        raw = str(tr)
        nid = None
        for pat in (r'data-node-nid="(\d+)"', r'data-nid="(\d+)"', r"/node/(?:company-press-release/)?(\d+)",
                    r"company-press-release/\d+/(\d+)", r'data-(?:id|press-release-id)="(\d+)"'):
            m = re.search(pat, raw)
            if m:
                nid = m.group(1)
                break
        a = next((x for x in tr.find_all("a", href=True) if x["href"].startswith(("/", "http"))), None)
        href = a["href"] if a else ""
        if href and href.startswith("/"):
            href = BASE + href
        topic = texts[-1] if len(texts) >= 4 and title_i != len(texts) - 1 else ""
        out.append({"date": when, "title": title, "topic": topic, "nid": nid, "href": href})
    return out


class EuronextSource(FilingSource):
    regulator = "Euronext"
    attribution = "Source: Euronext company press releases (personal use)"
    backfill_days = 180
    incremental_days = 5
    resolve_in_app = True
    _http = None

    def __init__(self, market: str, country: str, city: str, mics: tuple[str, ...], suffix: str, exchange: str, lang: str,
                 tz: str, news: dict, example: str):
        self.market, self.country, self.city, self.mics, self.suffix, self.exchange = market, country, city, mics, suffix, exchange
        self.source_lang, self.timezone, self.news_local = lang, tz, news
        self.ticker_hint = f"{city} ticker, e.g. {example}"

    @property
    def http(self) -> PoliteClient:
        if EuronextSource._http is None:          # one client and one pause shared by all Euronext markets
            EuronextSource._http = PoliteClient("euronext", pause=0.8, headers={"Referer": f"{BASE}/en"})
        return EuronextSource._http

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_EURONEXT", "false")).lower() in {"1", "true", "yes"}

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(re.escape(self.suffix) + "$", "", (raw or "").strip().upper()).replace(" ", "")
        return t if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", t) else None

    # -- finding the company on Euronext ----------------------------------------------------------------------
    def _instrument(self, query: str) -> str | None:
        """'<ISIN>-<MIC>' for the share on this market, from Euronext's own instrument search."""
        try:
            r = self.http.get(f"{BASE}/en/instrumentSearch/searchJSON", params={"q": query})
            items = r.json() if "json" in (r.headers.get("content-type") or "") or r.text.strip().startswith("[") else []
        except (ValueError, json.JSONDecodeError):
            items = []
        values = []
        for it in items if isinstance(items, list) else []:
            v = str(it.get("value") or "")
            if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d-[A-Z]{4}", v) and it.get("isin") and it.get("mic"):
                v = f'{it["isin"]}-{it["mic"]}'
            m = re.search(r"[A-Z]{2}[A-Z0-9]{9}\d-[A-Z]{4}", v or str(it.get("link") or it.get("url") or ""))
            v = m.group(0) if m else v
            if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d-[A-Z]{4}", v):
                values.append((v, str(it.get("label") or "") + " " + str(it.get("symbol") or "")))
        mine = [(v, label) for v, label in values if v.split("-")[-1] in self.mics]
        exact = [v for v, label in mine if re.search(rf"\b{re.escape(query)}\b", label, re.I)]
        return (exact or [v for v, _ in mine] or [None])[0]

    def _issuer_id(self, instrument: str) -> str | None:
        html = self.http.get(f"{BASE}/en/product/equities/{instrument}").text
        m = re.search(r"company-press-release/(\d+)", html)
        return m.group(1) if m else None

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        if not t:
            return None
        details = symbol_details(f"{t}{self.suffix}")
        if not details:
            return None
        name = details["name"]
        instrument = self._instrument(t) or self._instrument(name)
        issuer = self._issuer_id(instrument) if instrument else None
        if not issuer:
            log.warning("Euronext press releases not found for %s:%s (%s)", self.market, t, instrument or "no ISIN")
            return None
        return Company(self.market, t, issuer, name, name)

    def search(self, query: str, limit: int = 8) -> list[Company]:
        from services.prices import yahoo_search
        out = []
        for hit in yahoo_search(query, (self.exchange,), limit):
            t = hit["symbol"].removesuffix(self.suffix)
            name = hit.get("longname") or hit.get("shortname") or t
            out.append(Company(self.market, t, "", name, name))     # Euronext id is found when it's added
        return out

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("Euronext press releases", f"{BASE}/en/listview/company-press-release/{company.source_id}")]

    # -- press releases -------------------------------------------------------------------------------------------
    def _uid(self, company_id: str, row: dict) -> str:
        if row.get("nid"):
            return f"{self.market}:{row['nid']}"
        digest = hashlib.sha1(f"{company_id}|{row['date']}|{row['title']}".encode()).hexdigest()[:16]
        return f"{self.market}:h{digest}"

    def _url(self, company_id: str, row: dict) -> str:
        if row.get("href") and "listview" not in row["href"]:
            return row["href"]
        if row.get("nid"):
            return f"{BASE}/en/node/{row['nid']}"
        return f"{BASE}/en/listview/company-press-release/{company_id}"

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        if not company.source_id:
            found = self.resolve(company.ticker)
            if not found:
                return []
            company = found
        out = []
        for page in range(4):
            html = self.http.get(f"{BASE}/en/listview/company-press-release/{company.source_id}",
                                 params={"page": page} if page else None).text
            rows = parse_press_list(html)
            if not rows:
                break
            for r in rows:
                if start <= r["date"] <= end:
                    title = r["title"]
                    out.append(Filing(uid=self._uid(company.source_id, r), market=self.market, ticker=company.ticker,
                                      company_name=company.name_en, filed_date=r["date"], title_local=title,
                                      filer=r.get("topic") or "", url=self._url(company.source_id, r),
                                      title_en=title if title.isascii() else ""))
            if min(r["date"] for r in rows) < start:
                break
        return out

    def fetch_document_text(self, uid: str) -> str:
        from sqlalchemy import select
        from core.db import filings, get_engine
        nid = uid.split(":", 1)[1]
        if nid.isdigit():
            for url in (f"{BASE}/ajax/node/company-press-release/{nid}", f"{BASE}/en/node/{nid}"):
                try:
                    r = self.http.get(url)
                    text = html_text(r.text) if "html" in (r.headers.get("content-type") or "html") else any_text(r.content, r.headers.get("content-type") or "")
                    if len(text) > 200:
                        return text
                except Exception:
                    continue
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url or "listview" in url:
            return ""
        r = self.http.get(url)
        return any_text(r.content, r.headers.get("content-type") or "")


# ---- Italy: eMarket Storage (the authorised storage most Milan companies use), Euronext list as backup ------------
EMARKET = "https://www.emarketstorage.it"
IT_LEGAL = re.compile(r"\b(S\.?\s?P\.?\s?A\.?|S\.?R\.?L\.?|SOCIETA'?|PER AZIONI|N\.?V\.?|HOLDING|GROUP|GRUPPO)\b\.?", re.I)


def it_plain(name: str) -> str:
    n = IT_LEGAL.sub(" ", (name or "").upper().replace(",", " "))
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", n).split())


def parse_emarket(html: str) -> list[dict]:
    """Rows of an eMarket Storage list: {date, company, company_id, title, url, protocol}."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href*="/comunicati/"][href$=".pdf"]'):
        href = a["href"] if a["href"].startswith("http") else EMARKET + a["href"]
        if href in seen:
            continue
        box, text = a, ""
        for _ in range(6):
            if box.parent is None:
                break
            box = box.parent
            text = " ".join(box.get_text(" ").split())
            if re.search(r"\d{1,2}/\d{1,2}/\d{4}", text) and box.select_one('a[href*="azienda="]'):
                break
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if not m:
            continue
        a1, a2, y = int(m[1]), int(m[2]), int(m[3])
        try:      # the English site writes MM/DD/YYYY; fall back to DD/MM if that's impossible
            when = date(y, a1, a2) if a1 <= 12 else date(y, a2, a1)
        except ValueError:
            continue
        co = box.select_one('a[href*="azienda="]')
        cid = re.search(r"azienda=(\d+)", co["href"]).group(1) if co else ""
        company = " ".join(co.get_text(" ").split()) if co else ""
        if not company and co and co.find("img"):
            company = co.find("img").get("alt") or ""
        title = " ".join(a.get_text(" ").split())
        proto = re.search(r"_(\d+)\.pdf$", href)
        seen.add(href)
        out.append({"date": when, "company": company, "company_id": cid, "title": title or "Regulated information",
                    "url": href, "protocol": proto.group(1) if proto else hashlib.sha1(href.encode()).hexdigest()[:12]})
    return out


class ItalySource(EuronextSource):
    """Milan: regulated information from eMarket Storage, matched by company name; Euronext press releases when
    the company isn't on eMarket (some Italian companies use another storage service)."""
    regulator = "eMarket Storage / Euronext"
    attribution = "Source: eMarket Storage (Teleborsa) and Euronext (personal use)"
    _em = None

    @property
    def em(self) -> PoliteClient:
        if ItalySource._em is None:
            ItalySource._em = PoliteClient("emarket", pause=1.0, headers={"Referer": f"{EMARKET}/en"})
        return ItalySource._em

    def _em_list(self, **params) -> list[dict]:
        base = {"cerca": "", "titolo": "", "data_from": "", "data_to": "", "categoria": "All", "azienda": "",
                "mercato": "", "tipo_ricerca": ""}
        return parse_emarket(self.em.get(f"{EMARKET}/en/node/21", params={**base, **params}).text)

    def _em_companies(self) -> dict[str, str]:
        """eMarket company ids by plain name: the list's company filter, or else the issuers on recent pages."""
        if getattr(self, "_em_ids", None) is not None:
            return self._em_ids
        from bs4 import BeautifulSoup
        ids: dict[str, str] = {}
        try:
            soup = BeautifulSoup(self.em.get(f"{EMARKET}/en/node/21").text, "html.parser")
            for opt in soup.select('select[name="azienda"] option'):
                if (opt.get("value") or "").isdigit():
                    ids.setdefault(it_plain(opt.get_text()), opt["value"])
            if not ids:
                for page in range(12):
                    for r in self._em_list(page=page):
                        if r["company_id"] and r["company"]:
                            ids.setdefault(it_plain(r["company"]), r["company_id"])
        except Exception as exc:
            if exc.__class__.__name__ == "SourceBusy":
                raise
            log.warning("eMarket company list failed for IT: %s", exc)
        self._em_ids = ids
        return ids

    def _em_id(self, name: str) -> str | None:
        want = it_plain(name)
        ids = self._em_companies()
        if want in ids:
            return ids[want]
        close = [k for k in ids if k.split()[:1] == want.split()[:1] and (k.startswith(want) or want.startswith(k))]
        return ids[min(close, key=len)] if close else None

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        details = symbol_details(f"{t}{self.suffix}") if t else None
        if not details:
            return None
        cid = self._em_id(details["name"])
        if cid:
            return Company(self.market, t, f"em:{cid}", details["name"], details["name"])
        return super().resolve(ticker)

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        if company.source_id.startswith("em:"):
            return [("eMarket Storage", f"{EMARKET}/en/node/21?azienda={company.source_id[3:]}")]
        return super().external_links(company)

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        if not company.source_id.startswith("em:"):
            return super().list_filings(company, start, end)
        out = []
        for page in range(5):
            cid = company.source_id[3:]
            rows = self._em_list(azienda=cid, page=page)
            for r in rows:
                if start <= r["date"] <= end and r["company_id"] in ("", cid):
                    title = r["title"]
                    out.append(Filing(uid=f"IT:em{r['protocol']}", market=self.market, ticker=company.ticker,
                                      company_name=company.name_en, filed_date=r["date"], title_local=title,
                                      filer="Regulated information", url=r["url"],
                                      title_en=title if title.isascii() and not re.search(r"\b(di|del|della|e|per)\b", title) else ""))
            if not rows or min(r["date"] for r in rows) < start:
                break
        return out

    def market_feed(self) -> list[dict]:
        out = []
        for page in range(3):
            for r in self._em_list(page=page):
                if r["date"] >= self.today() - timedelta(days=1):
                    out.append({"uid": f"IT:em{r['protocol']}", "ticker": "", "company": r["company"],
                                "title_local": r["title"], "title_en": r["title"] if r["title"].isascii() else "",
                                "url": r["url"], "date": r["date"]})
        return out

    def fetch_document_text(self, uid: str) -> str:
        if not uid.startswith("IT:em"):
            return super().fetch_document_text(uid)
        from sqlalchemy import select
        from core.db import filings, get_engine
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url:
            return ""
        r = self.em.get(url)
        return any_text(r.content, r.headers.get("content-type") or "")


def euronext_sources() -> list[EuronextSource]:
    return [
        EuronextSource("NL", "Netherlands", "Amsterdam", ("XAMS", "TNLA", "ALXA"), ".AS", "AMS", "nl", "Europe/Amsterdam",
                       {"hl": "nl", "gl": "NL", "ceid": "NL:nl"}, "ASML"),
        EuronextSource("BE", "Belgium", "Brussels", ("XBRU", "ALXB", "MLXB", "ENXB"), ".BR", "BRU", "fr", "Europe/Brussels",
                       {"hl": "nl", "gl": "BE", "ceid": "BE:nl"}, "UCB"),
        EuronextSource("PT", "Portugal", "Lisbon", ("XLIS", "ALXL", "ENXL"), ".LS", "LIS", "pt", "Europe/Lisbon",
                       {"hl": "pt-PT", "gl": "PT", "ceid": "PT:pt-150"}, "GALP"),
        EuronextSource("IE", "Ireland", "Dublin", ("XMSM", "XESM"), ".IR", "ISE", "en", "Europe/Dublin",
                       {"hl": "en-IE", "gl": "IE", "ceid": "IE:en"}, "KRZ"),
        ItalySource("IT", "Italy", "Milan", ("MTAA", "EXGM", "ETLX", "MIVX"), ".MI", "MIL", "it", "Europe/Rome",
                       {"hl": "it", "gl": "IT", "ceid": "IT:it"}, "ENI"),
    ]
