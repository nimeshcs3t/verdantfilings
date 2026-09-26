"""Automatic filing categories from titles (English and original language). Rule order matters."""
from __future__ import annotations

import re

CATEGORIES = {
    "buyback":   ("Buyback", ["buyback", "treasury share", "repurchase", "own shares", "skup akcji", "nabycie akcji własnych",
                              "자기주식", "自己株"]),
    "dividend":  ("Dividend", ["dividend", "distribution per share", "dywidend", "배당", "配当"]),
    "insider":   ("Insider trade", ["insider", "form 4", "form 3", "form 5", "form 144", "director's interest",
                                   "director interest", "art. 19", "art 19 mar", "managers' transactions", "transakcj",
                                   "임원ㆍ주요주주", "임원·주요주주"]),
    "ownership": ("Stake change", ["large shareholding", "5% rule", "13d", "13g", "substantial holder", "change in holding",
                                   "stan posiadania", "liczbie głosów", "total number of votes", "voting rights", "major holding",
                                   "largest shareholder", "대량보유", "최대주주", "大量保有", "voting share"]),
    "capital":   ("Capital raise", ["rights offering", "capital increase", "placement", "share issue", "issuance",
                                   "convertible", "bond with warrants", "prospectus", "registration statement",
                                   "capital raising", "entitlement offer", "emisj", "podwyższeni", "유상증자",
                                   "전환사채", "신주인수권", "shelf"]),
    "mna":       ("M&A", ["merger", "acquisition", "acquire", "disposal", "takeover", "tender offer", "scheme of arrangement",
                          "demerger", "split decision", "spin-off", "podział", "połączeni", "nabyci", "zbyci", "합병",
                          "분할", "인수", "타법인주식", "公開買付"]),
    "contract":  ("Contract", ["contract", "agreement", "order", "umow", "zamówieni", "계약"]),
    "earnings":  ("Earnings", ["results", "earnings", "preliminary", "revenue", "profit", "ebitda", "sales",
                               "operating performance", "wyniki", "przychod", "실적", "決算"]),
    "periodic":  ("Report", ["annual report", "half-year", "semi-annual", "quarterly report", "periodic report",
                             "securities report", "10-k", "10-q", "20-f", "40-f", "raport roczny", "raport kwartalny",
                             "raport półroczny", "raport okresowy", "사업보고서", "분기보고서", "반기보고서"]),
    "meeting":   ("Meeting", ["general meeting", "agm", "egm", "proxy", "shareholders' meeting", "shareholder vote",
                              "walne", "zgromadzeni", "주주총회"]),
    "board":     ("Board", ["director", "officer", "board", "management board", "appointment", "resignation", "auditor",
                            "zarząd", "rada nadzorcza", "powołani", "odwołani"]),
    "legal":     ("Legal", ["lawsuit", "litigation", "court", "sanction", "penalty", "restructuring", "bankruptcy",
                            "insolvency", "arrangement proceedings", "kara", "upadłoś", "restrukturyzac", "소송"]),
}
MAJOR = {"buyback", "dividend", "ownership", "capital", "mna", "contract", "earnings", "periodic", "legal"}
ORDER = list(CATEGORIES) + ["other"]


def categorize(title_en: str | None, title_local: str | None = "", price_sensitive: bool = False) -> str:
    text = f" {title_en or ''} | {title_local or ''} ".lower()
    if re.search(r"\b8-k\b.*results|item 2\.02|results \(earnings\)", text):
        return "earnings"
    for key, (_, words) in CATEGORIES.items():
        if any(w in text for w in words):
            return key
    return "earnings" if price_sensitive else "other"


def label(key: str) -> str:
    return CATEGORIES.get(key, ("Other", []))[0]


def is_major(row: dict) -> bool:
    return bool(row.get("price_sensitive")) or categorize(row.get("title_en"), row.get("title_local")) in MAJOR
