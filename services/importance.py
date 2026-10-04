"""Importance score (0-100) for each filing, so the day's most significant ones can come first."""
from __future__ import annotations

import re

from .classify import categorize

BASE = {"mna": 80, "earnings": 72, "capital": 70, "contract": 62, "buyback": 60, "dividend": 58, "legal": 60,
        "periodic": 55, "ownership": 48, "board": 40, "insider": 35, "meeting": 30, "other": 20}
STRONG = re.compile(r"\b(takeover|tender offer|merger|acquisition|acquire|guidance|profit warning|downgrade|upgrade|"
                    r"bankrupt|insolven|delist|suspen|halt|restat|record|largest|strategic review|spin-off|"
                    r"rights issue|placement|investigation|fda|approval|recall)\w*", re.I)
BIG_NUMBER = re.compile(r"\b\d[\d,.]*\s*(bn|billion|trillion|million|m\b|조|억)", re.I)


def score(row: dict) -> int:
    text = f"{row.get('title_en') or ''} {row.get('summary_en') or ''}"
    s = BASE.get(categorize(row.get("title_en"), row.get("title_local"), row.get("price_sensitive")), 20)
    if row.get("price_sensitive"):
        s += 20
    s += min(2, len(STRONG.findall(text))) * 8
    if BIG_NUMBER.search(text):
        s += 5
    return max(0, min(100, s))


def label(value: int) -> str:
    return "High" if value >= 75 else "Medium" if value >= 50 else "Low"
