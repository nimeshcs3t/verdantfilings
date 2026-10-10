"""Upcoming dates announced in filings: general meetings, record and payment dates, results releases,
subscription and listing dates. Extracted by Gemini/Claude when available, else by date patterns."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from sqlalchemy import and_, delete, insert, or_, select
from sqlalchemy.exc import IntegrityError

from core.db import events, events_scanned, filings, get_engine, watchlist

from .classify import categorize

log = logging.getLogger(__name__)
EVENT_CATEGORIES = {"meeting", "dividend", "earnings", "capital", "mna", "buyback", "periodic", "other"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
LABELS = [(("general meeting", "agm", "egm", "shareholders' meeting", "meeting of shareholders"), "General meeting"),
          (("record date", "cut-off date", "ex-dividend", "ex dividend"), "Record date"),
          (("payment date", "payable", "paid on", "to be paid"), "Payment date"),
          (("results", "earnings", "financial statements will", "announcement of"), "Results release"),
          (("subscription", "offer period", "tender period"), "Subscription period"),
          (("listing date", "listed on", "trading will commence"), "Listing date")]
PROMPT = """From this stock-exchange filing, list future dates that investors would want in a calendar:
general meetings, record dates, ex-dividend and payment dates, results releases, subscription or offer periods,
listing dates, redemption or conversion dates. Use only dates stated in the text.
Reply with JSON only: [{{"date": "YYYY-MM-DD", "event": "short label"}}] or [] if there are none.

Filing: {title}
Filed on: {filed}
Text:
{text}"""


def _regex_dates(text: str) -> list[tuple[date, str]]:
    found = []
    for sentence in re.split(r"(?<=[.;])\s+|\n", text or ""):
        low = sentence.lower()
        label = next((lab for words, lab in LABELS if any(w in low for w in words)), None)
        if not label:
            continue
        for m in re.finditer(r"(\d{1,2}) (january|february|march|april|may|june|july|august|september|october|"
                             r"november|december) (\d{4})", low):
            found.append((date(int(m[3]), MONTHS[m[2]], int(m[1])), label))
        for m in re.finditer(r"(january|february|march|april|may|june|july|august|september|october|november|"
                             r"december) (\d{1,2}),? (\d{4})", low):
            found.append((date(int(m[3]), MONTHS[m[1]], int(m[2])), label))
        for m in re.finditer(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", low):
            try:
                found.append((date(int(m[1]), int(m[2]), int(m[3])), label))
            except ValueError:
                pass
    return found


def _llm_dates(row: dict) -> list[tuple[date, str]] | None:
    from .summarize import _anthropic, _gemini, providers
    text = "\n".join(x for x in (row.get("summary_en"), (row.get("body_en") or "")[:8000]) if x)
    prompt = PROMPT.format(title=row["title_en"], filed=row["filed_date"], text=text)
    for provider in providers():
        try:
            reply = provider(prompt)
        except Exception:
            continue
        if not reply:
            continue
        m = re.search(r"\[.*\]", reply, re.S)
        try:
            items = json.loads(m.group(0)) if m else []
        except ValueError:
            continue
        out = []
        for it in items:
            try:
                out.append((date.fromisoformat(str(it["date"])[:10]), str(it["event"])[:120]))
            except (KeyError, ValueError, TypeError):
                continue
        return out
    return None


def refresh(limit: int = 15) -> int:
    """Worker: scan new overviews of watched companies for dates."""
    with get_engine().connect() as conn:
        watched = [tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).distinct())]
        if not watched:
            return 0
        cond = or_(*[and_(filings.c.market == m, filings.c.ticker == t) for m, t in watched])
        rows = [dict(r) for r in conn.execute(select(filings).where(
            cond, filings.c.summary_en.is_not(None), filings.c.filed_date >= date.today() - timedelta(days=120),
            filings.c.uid.not_in(select(events_scanned.c.uid))).order_by(filings.c.filed_date.desc()).limit(limit * 3)).mappings()]
    done = 0
    for row in rows:
        if done >= limit:
            break
        if categorize(row["title_en"], row["title_local"]) not in EVENT_CATEGORIES:
            _mark(row["uid"])
            continue
        found = _llm_dates(row)
        if found is None:
            found = _regex_dates("\n".join(x for x in (row.get("summary_en"), row.get("body_en")) if x))
        with get_engine().begin() as conn:
            conn.execute(delete(events).where(events.c.uid == row["uid"]))
            for day, label in {(d, l) for d, l in found if d >= row["filed_date"]}:
                conn.execute(insert(events).values(uid=row["uid"], event_date=day, label=label,
                                                   market=row["market"], ticker=row["ticker"]))
        _mark(row["uid"])
        done += 1
    return done


def _mark(uid: str) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(events_scanned).values(uid=uid))
    except IntegrityError:
        pass


def upcoming(pairs: list[tuple[str, str]], days: int = 60) -> list[dict]:
    if not pairs:
        return []
    cond = or_(*[and_(events.c.market == m, events.c.ticker == t) for m, t in pairs])
    today = date.today()
    with get_engine().connect() as conn:
        rows = conn.execute(select(events, filings.c.company_name, filings.c.url, filings.c.title_en)
                            .select_from(events.join(filings, filings.c.uid == events.c.uid))
                            .where(cond, events.c.event_date >= today, events.c.event_date <= today + timedelta(days=days))
                            .order_by(events.c.event_date)).mappings().all()
    seen, out = set(), []
    for r in rows:
        key = (r["market"], r["ticker"], r["event_date"], r["label"].lower())
        if key not in seen:
            seen.add(key)
            out.append(dict(r))
    return out


def between(pairs: list[tuple[str, str]], start: date, end: date) -> list[dict]:
    """Events (past or future) between two dates for these companies, without duplicates."""
    if not pairs:
        return []
    cond = or_(*[and_(events.c.market == m, events.c.ticker == t) for m, t in pairs])
    with get_engine().connect() as conn:
        rows = conn.execute(select(events, filings.c.company_name, filings.c.url, filings.c.title_en)
                            .select_from(events.join(filings, filings.c.uid == events.c.uid))
                            .where(cond, events.c.event_date >= start, events.c.event_date <= end)
                            .order_by(events.c.event_date)).mappings().all()
    seen, out = set(), []
    for r in rows:
        key = (r["market"], r["ticker"], r["event_date"], r["label"].lower())
        if key not in seen:
            seen.add(key)
            out.append(dict(r))
    return out


def kind(label: str) -> str:
    """Group an event label for colours and filters: results, meeting, dividend or other."""
    low = (label or "").lower()
    if any(w in low for w in ("result", "earning", "financial statement", "report date")):
        return "results"
    if any(w in low for w in ("meeting", "agm", "egm", "vote")):
        return "meeting"
    if any(w in low for w in ("dividend", "record", "payment", "ex-", "distribution")):
        return "dividend"
    return "other"


def expected_results(pairs: list[tuple[str, str]], start: date, end: date) -> list[dict]:
    """Results dates for these companies: announced dates where known, otherwise an estimate from the company's own
    reporting rhythm (about 3 months after the last results if it reports quarterly, 6 if half-yearly)."""
    if not pairs:
        return []
    out, today = [], date.today()
    confirmed = [e for e in between(pairs, start - timedelta(days=30), end + timedelta(days=30)) if kind(e["label"]) == "results"]
    out += [{**e, "estimate": False} for e in confirmed if start <= e["event_date"] <= end]
    with get_engine().connect() as conn:
        for m, t in pairs:
            rows = conn.execute(select(filings.c.filed_date, filings.c.title_en, filings.c.title_local, filings.c.company_name,
                                       filings.c.url).where(filings.c.market == m, filings.c.ticker == t,
                                                            filings.c.filed_date >= today - timedelta(days=400))
                                .order_by(filings.c.filed_date)).all()
            dates = []
            for d, title_en, title_local, *_ in rows:
                if categorize(title_en, title_local) in ("earnings", "periodic") and (not dates or (d - dates[-1]).days > 20):
                    dates.append(d)
            if not dates:
                continue
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            cadence = 91 if any(60 <= g <= 120 for g in gaps) else 182 if gaps or m in ("AU", "UK", "HK", "IL") else 91
            nxt = dates[-1] + timedelta(days=cadence)
            while nxt < today:
                nxt += timedelta(days=cadence)
            if not start <= nxt <= end:
                continue
            if any(e["market"] == m and e["ticker"] == t and abs((e["event_date"] - nxt).days) <= 25 for e in confirmed):
                continue        # the company has announced a date; no estimate needed
            name = rows[-1][3]
            out.append({"market": m, "ticker": t, "company_name": name, "event_date": nxt, "label": "Results (expected)",
                        "url": rows[-1][4], "estimate": True})
    return sorted(out, key=lambda e: e["event_date"])
