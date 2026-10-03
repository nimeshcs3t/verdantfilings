"""Telegram alerts: instant (respecting each member's choices), keyword matches, daily digest, weekly report."""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from core.config import direct_fetch, get_secret
from core.db import (filings, get_engine, keyword_hits, keywords, listed_companies, user_prefs, users, utcnow,
                     watch_prefs, watchlist)
from sources import configured_sources, get_source

from . import personal, telegram
from .classify import categorize, is_major, label
from .translate import glossary_or_cached, translate_keyword, translate_lines

log = logging.getLogger(__name__)
MAX_KEYWORD_MESSAGES = 15
esc = lambda s: html.escape(str(s or ""), quote=False)


def _members() -> dict[int, dict]:
    """Active members with Telegram connected, with their preferences."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(users.c.id, users.c.username, users.c.telegram_chat_id).where(
            users.c.is_active == True, users.c.telegram_chat_id.is_not(None))).mappings().all()  # noqa: E712
    return {r["id"]: {**personal.get_prefs(r["id"]), "chat": r["telegram_chat_id"], "username": r["username"]}
            for r in rows}


def _watchers(market: str, ticker: str) -> dict[int, str]:
    """user_id -> alert level, for members watching this company with alerts on."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(watchlist.c.user_id, watch_prefs.c.level).select_from(
            watchlist.outerjoin(watch_prefs, and_(watch_prefs.c.user_id == watchlist.c.user_id,
                                                  watch_prefs.c.market == watchlist.c.market,
                                                  watch_prefs.c.ticker == watchlist.c.ticker)))
            .where(watchlist.c.market == market, watchlist.c.ticker == ticker,
                   watchlist.c.notify == True)).all()  # noqa: E712
    return {r[0]: (r[1] or "all") for r in rows}


def wants(prefs: dict, level: str, row: dict) -> bool:
    if level == "major" and not is_major(row):
        return False
    if prefs.get("skip_insider") and categorize(row.get("title_en"), row.get("title_local")) == "insider":
        return False
    return True


# ---- instant alerts -------------------------------------------------------------------------
def notify_pending() -> int:
    from .pipeline import enrich_filing, price_sensitive_uids
    with get_engine().connect() as conn:
        pending = [dict(r) for r in conn.execute(
            select(filings).where(filings.c.notified == False).order_by(filings.c.uid)).mappings()]  # noqa: E712
    members = _members() if pending else {}
    channel = get_secret("TELEGRAM_CHANNEL_ID")
    sent = 0
    for row in pending:
        src = get_source(row["market"])
        recent = src is not None and row["filed_date"] >= src.today() - timedelta(days=1)
        row["price_sensitive"] = row["uid"] in price_sensitive_uids([row["uid"]])
        if recent and telegram.enabled() and src.should_alert(row):
            if not row["summary_en"] and direct_fetch():
                row = {**row, **(enrich_filing(row["uid"]) or {})}
            chats = set()
            for user_id, level in _watchers(row["market"], row["ticker"]).items():
                prefs = members.get(user_id)
                if prefs and prefs["alert_mode"] == "instant" and wants(prefs, level, row):
                    chats.add(prefs["chat"])
            if channel and not getattr(src, "admin_only", False):
                chats.add(channel)
            message = telegram.format_filing(row)
            for chat in chats:
                sent += telegram.send_message(chat, message)
                time.sleep(0.05)
        with get_engine().begin() as conn:
            conn.execute(update(filings).where(filings.c.uid == row["uid"]).values(notified=True))
    return sent


# ---- keyword alerts -------------------------------------------------------------------------
def _names(market: str, items: list[dict]) -> None:
    """Fill ticker and English company name from the stored listings."""
    ids = {i.get("source_id") for i in items if i.get("source_id")}
    tickers = {i.get("ticker") for i in items if i.get("ticker")}
    if not ids and not tickers:
        return
    with get_engine().connect() as conn:
        rows = conn.execute(select(listed_companies).where(listed_companies.c.market == market, or_(
            listed_companies.c.source_id.in_(ids or {""}), listed_companies.c.ticker.in_(tickers or {""})))).mappings().all()
    by_id = {r["source_id"]: r for r in rows}
    by_ticker = {r["ticker"]: r for r in rows}
    for i in items:
        r = by_id.get(i.get("source_id")) or by_ticker.get(i.get("ticker"))
        if r:
            i["ticker"] = i.get("ticker") or r["ticker"]
            i["company"] = r["name_en"] or i.get("company")


def _candidates() -> list[dict]:
    """New filings from tracked companies plus each market's latest filings."""
    items: dict[str, dict] = {}
    cutoff = utcnow() - timedelta(days=2)
    with get_engine().connect() as conn:
        for r in conn.execute(select(filings).where(filings.c.created_at >= cutoff)).mappings():
            items[r["uid"]] = {"uid": r["uid"], "market": r["market"], "ticker": r["ticker"],
                               "company": r["company_name"], "title_local": r["title_local"] or "",
                               "title_en": r["title_en"] or "", "url": r["url"], "date": r["filed_date"]}
    for src in configured_sources():
        try:
            feed = src.market_feed()
        except Exception as exc:
            log.warning("market feed failed for %s: %s", src.market, exc)
            continue
        feed = [f for f in feed if f.get("date") and f["date"] >= src.today() - timedelta(days=1)]
        _names(src.market, feed)
        for f in feed:
            if f["uid"] not in items:
                f["market"] = src.market
                f["title_en"] = f.get("title_en") or glossary_or_cached(f["title_local"], src.source_lang) or ""
                items[f["uid"]] = f
    return list(items.values())


def keyword_alerts() -> int:
    with get_engine().connect() as conn:
        words = [dict(r) for r in conn.execute(select(keywords)).mappings()]
    if not words or not telegram.enabled():
        return 0
    members = _members()
    words = [w for w in words if w["user_id"] in members]
    if not words:
        return 0
    candidates = _candidates()
    local_terms: dict[tuple[str, str], str | None] = {}
    matches: dict[int, list[tuple[str, dict]]] = {}
    for w in words:
        needle = w["keyword"].lower()
        for item in candidates:
            if w["market"] not in ("*", item["market"]):
                continue
            src = get_source(item["market"])
            lang = src.source_lang if src else "en"
            key = (w["keyword"], lang)
            if key not in local_terms:
                local_terms[key] = (translate_keyword(w["keyword"], lang) or "").lower() or None
            local = local_terms[key]
            text_en, text_local = item["title_en"].lower(), item["title_local"].lower()
            if needle in text_en or needle in text_local or (local and local in text_local):
                matches.setdefault(w["user_id"], []).append((w["keyword"], item))
    sent = 0
    for user_id, hits in matches.items():
        fresh = []
        for word, item in hits:
            try:
                with get_engine().begin() as conn:
                    conn.execute(insert(keyword_hits).values(user_id=user_id, uid=item["uid"], created_at=utcnow()))
                fresh.append((word, item))
            except IntegrityError:
                continue
        if not fresh:
            continue
        for market in {i["market"] for _, i in fresh}:
            todo = [i for _, i in fresh if i["market"] == market and not i["title_en"]]
            src = get_source(market)
            if todo and src:
                for i, en in zip(todo, translate_lines([i["title_local"] for i in todo], src.source_lang)):
                    i["title_en"] = en
        chat = members[user_id]["chat"]
        for word, item in fresh[:MAX_KEYWORD_MESSAGES]:
            country = get_source(item["market"]).country if get_source(item["market"]) else item["market"]
            message = (f"Keyword match: <b>{esc(word)}</b>\n<b>{esc(item['company'])}</b>  {esc(item.get('ticker'))}  "
                       f"{esc(country)}\n{esc(item['title_en'] or item['title_local'])}\n\n"
                       f'<a href="{html.escape(item["url"])}">Original filing</a>')
            sent += telegram.send_message(chat, message)
            time.sleep(0.05)
        if len(fresh) > MAX_KEYWORD_MESSAGES:
            telegram.send_message(chat, f"…and {len(fresh) - MAX_KEYWORD_MESSAGES} more keyword matches. "
                                        "Search the app to see them all.")
    return sent


# ---- daily digest and weekly report -----------------------------------------------------------
def _member_filings(user_id: int, prefs: dict, since: datetime) -> list[dict]:
    with get_engine().connect() as conn:
        watched = conn.execute(select(watchlist.c.market, watchlist.c.ticker).where(
            watchlist.c.user_id == user_id, watchlist.c.notify == True)).all()  # noqa: E712
        if not watched:
            return []
        cond = or_(*[and_(filings.c.market == m, filings.c.ticker == t) for m, t in watched])
        rows = [dict(r) for r in conn.execute(select(filings).where(cond, filings.c.created_at >= since)
                                              .order_by(filings.c.company_name, filings.c.filed_date)).mappings()]
    from .pipeline import price_sensitive_uids
    flagged = price_sensitive_uids([r["uid"] for r in rows])
    lv = personal.levels(user_id)
    out = []
    for r in rows:
        r["price_sensitive"] = r["uid"] in flagged
        if wants(prefs, lv.get((r["market"], r["ticker"]), "all"), r):
            out.append(r)
    return out


def _send_long(chat: str, lines: list[str]) -> int:
    sent, chunk = 0, ""
    for line in lines:
        if len(chunk) + len(line) > 3800:
            sent += telegram.send_message(chat, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        sent += telegram.send_message(chat, chunk)
    return sent


def daily_digests() -> int:
    sent = 0
    app_url = get_secret("APP_URL")
    for user_id, prefs in _members().items():
        if prefs["alert_mode"] != "digest":
            continue
        now = datetime.now(ZoneInfo(prefs["tz"] or "UTC"))
        if now.hour < int(prefs["digest_hour"]) or prefs["last_digest"] == now.date().isoformat():
            continue
        rows = _member_filings(user_id, prefs, utcnow() - timedelta(days=1))
        lines = [f"<b>Your filings digest</b>  {now:%d %b %Y}"]
        if not rows:
            lines.append("No new filings from your companies in the last 24 hours.")
        company = None
        for r in rows:
            if r["company_name"] != company:
                company = r["company_name"]
                lines += ["", f"<b>{esc(company)}</b>  {esc(r['ticker'])}"]
            tag = label(categorize(r["title_en"], r["title_local"], r.get("price_sensitive")))
            lines.append(f'• [{esc(tag)}] <a href="{html.escape(r["url"])}">{esc(r["title_en"])}</a>')
        if app_url:
            lines += ["", f'<a href="{html.escape(app_url)}">Open the app</a>']
        sent += _send_long(prefs["chat"], lines)
        personal.save_prefs(user_id, last_digest=now.date().isoformat())
    return sent


def weekly_reports() -> int:
    sent = 0
    for user_id, prefs in _members().items():
        if not prefs["weekly"]:
            continue
        now = datetime.now(ZoneInfo(prefs["tz"] or "UTC"))
        if now.weekday() != 0 or now.hour < int(prefs["digest_hour"]) or prefs["last_weekly"] == now.date().isoformat():
            continue
        rows = _member_filings(user_id, {**prefs, "skip_insider": False}, utcnow() - timedelta(days=7))
        counts: dict[str, int] = {}
        for r in rows:
            key = label(categorize(r["title_en"], r["title_local"], r.get("price_sensitive")))
            counts[key] = counts.get(key, 0) + 1
        companies = len({(r["market"], r["ticker"]) for r in rows})
        lines = [f"<b>Your week in filings</b>  {now - timedelta(days=7):%d %b} to {now:%d %b}", "",
                 f"{len(rows)} filings from {companies} of your companies."]
        if counts:
            lines.append(", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
        major = [r for r in rows if is_major(r)][:12]
        if major:
            lines += ["", "<b>Highlights</b>"]
            for r in major:
                lines.append(f'• {esc(r["company_name"])}: <a href="{html.escape(r["url"])}">{esc(r["title_en"])}</a>')
        sent += _send_long(prefs["chat"], lines)
        personal.save_prefs(user_id, last_weekly=now.date().isoformat())
    return sent


def run_alerts() -> dict:
    stats = {"alerts": 0, "keyword_alerts": 0, "digests": 0, "weekly": 0}
    for key, job in (("alerts", notify_pending), ("keyword_alerts", keyword_alerts),
                     ("digests", daily_digests), ("weekly", weekly_reports)):
        try:
            stats[key] = job()
        except Exception as exc:
            log.warning("%s failed: %s", key, exc)
    return stats
