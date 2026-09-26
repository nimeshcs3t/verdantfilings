"""Telegram commands, handled by the worker each run: /help /list /today /add /remove /portfolio /price.
Also completes the Account page's "connect Telegram" step (/start CODE)."""
from __future__ import annotations

import html
import logging
from datetime import timedelta

from sqlalchemy import insert, or_, select
from sqlalchemy.exc import IntegrityError

from core.auth import update_user
from core.db import companies, get_engine, listed_companies, users, watchlist
from core.usage import get_state, set_state
from sources import configured_sources, get_source

from . import portfolio, prices, telegram, watch

log = logging.getLogger(__name__)
esc = lambda s: html.escape(str(s or ""), quote=False)
HELP = ("<b>Commands</b>\n/list: your watchlist\n/today: today's filings\n/add &lt;ticker or name&gt; [country]: follow a "
        "company, e.g. /add 005930 or /add apple\n/remove &lt;ticker&gt;: stop following\n/portfolio: your holdings\n"
        "/price &lt;ticker&gt;: latest price\n\nReplies arrive within about 10 minutes.")


def _user_for_chat(chat: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(users).where(users.c.telegram_chat_id == chat, users.c.is_active == True)).mappings().first()  # noqa: E712
    return dict(row) if row else None


def _find(query: str, market: str | None = None) -> list[dict]:
    """Search the stored company listings (no calls to regulators)."""
    q = query.strip()
    live = {s.market for s in configured_sources()}
    with get_engine().connect() as conn:
        cond = [listed_companies.c.market.in_([market] if market else live)]
        exact = conn.execute(select(listed_companies).where(*cond, or_(
            listed_companies.c.ticker == q.upper(), listed_companies.c.ticker == q.upper().replace(".", "-")))).mappings().all()
        if exact:
            return [dict(r) for r in exact]
        return [dict(r) for r in conn.execute(select(listed_companies).where(*cond, or_(
            listed_companies.c.name_en.ilike(f"%{q}%"), listed_companies.c.name_local.ilike(f"%{q}%"))).limit(6)).mappings()]


def _market_from(words: list[str]) -> str | None:
    names = {s.country.lower(): s.market for s in configured_sources()}
    names.update({s.market.lower(): s.market for s in configured_sources()})
    last = words[-1].lower() if len(words) > 1 else ""
    return names.get(last)


def _ensure_company(row: dict) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(companies).values(market=row["market"], ticker=row["ticker"], source_id=row["source_id"],
                                                  name_local=row["name_local"], name_en=row["name_en"], last_synced=None))
    except IntegrityError:
        pass


def handle(chat: str, text: str) -> str | None:
    parts = text.split()
    command = parts[0].split("@")[0].lower() if parts else ""
    args = parts[1:]
    if command == "/start":
        if args:
            with get_engine().connect() as conn:
                row = conn.execute(select(users.c.id).where(users.c.tg_link_code == args[0])).first()
            if row:
                update_user(row[0], telegram_chat_id=chat, tg_link_code=None)
                return "Connected. You'll get alerts here for your watchlist.\n\n" + HELP
        return "Hello. Connect this chat from the app: Account, then Open the bot."
    if not command.startswith("/"):
        return None
    user = _user_for_chat(chat)
    if not user:
        return "This chat isn't connected to an account yet. Open the app, then Account, then Open the bot."
    if command == "/help":
        return HELP
    if command == "/list":
        wl = watch.get_watchlist(user["id"])
        if not wl:
            return "Your watchlist is empty. Add one with /add, e.g. /add 005930"
        return "<b>Your watchlist</b>\n" + "\n".join(f"{esc(w['name_en'])}  {esc(w['ticker'])}  "
                                                    f"({esc(get_source(w['market']).country)})" for w in wl)
    if command == "/today":
        from .pipeline import filings_for
        wl = watch.get_watchlist(user["id"])
        rows = []
        for m in {w["market"] for w in wl}:
            rows += filings_for([(w["market"], w["ticker"]) for w in wl if w["market"] == m],
                                get_source(m).today() - timedelta(days=0))
        if not rows:
            return "No filings from your companies today yet."
        lines = [f"<b>Today: {len(rows)} filing{'s' if len(rows) != 1 else ''}</b>"]
        lines += [f'• {esc(r["company_name"])}: <a href="{html.escape(r["url"])}">{esc(r["title_en"])}</a>' for r in rows[:25]]
        return "\n".join(lines)
    if command in ("/add", "/remove", "/price"):
        if not args:
            return f"Add a ticker or name, e.g. {command} 005930"
        market = _market_from(args)
        query = " ".join(args[:-1] if market else args)
        found = _find(query, market)
        if command == "/remove":
            wl = [w for w in watch.get_watchlist(user["id"]) if w["ticker"].upper() == query.upper()
                  and (not market or w["market"] == market)]
            if not wl:
                return f"{esc(query)} isn't on your watchlist."
            watch.remove(user["id"], wl[0]["market"], wl[0]["ticker"])
            return f"Removed {esc(wl[0]['name_en'])}."
        if not found:
            return f"No listed company matches {esc(query)}."
        if len(found) > 1:
            return "Which one?\n" + "\n".join(f"{command} {esc(r['ticker'])} {esc(get_source(r['market']).country)}  "
                                              f"({esc(r['name_en'])})" for r in found)
        row = found[0]
        if command == "/price":
            h = prices.history(row["market"], row["ticker"])
            if not h:
                return f"No price available for {esc(row['name_en'])}."
            move = prices.last_move(h)
            return (f"<b>{esc(row['name_en'])}</b>  {esc(row['ticker'])}\n{h[-1][1]:,.2f} "
                    f"{portfolio.CURRENCY.get(row['market'], '')}" + (f"  ({move * 100:+.1f}%)" if move is not None else "")
                    + f"\nas of {h[-1][0]:%d %b}")
        _ensure_company(row)
        _, err = watch.add(user, row["market"], row["ticker"])
        return esc(err) if err else f"Added {esc(row['name_en'])} ({esc(row['ticker'])}). Filings appear in the app shortly."
    if command == "/portfolio":
        items = portfolio.list_holdings(user["id"])
        if not items:
            return "No holdings yet. Add them on the app's Portfolio page."
        lines, totals = ["<b>Your portfolio</b>"], {}
        for h in items:
            v = portfolio.value(h, prices.history(h["market"], h["ticker"]))
            if v["value"] is None:
                lines.append(f"{esc(h['ticker'])}: no price")
                continue
            totals.setdefault(v["currency"], [0.0, 0.0])
            totals[v["currency"]][0] += v["value"]
            totals[v["currency"]][1] += v["cost"]
            lines.append(f"{esc(h['ticker'])}: {v['value']:,.0f} {v['currency']} ({v['gain_pct'] * 100:+.1f}%)")
        lines += [""] + [f"Total {c}: {val:,.0f} ({(val / cost - 1) * 100 if cost else 0:+.1f}%)"
                         for c, (val, cost) in totals.items()]
        return "\n".join(lines)
    return "Unknown command. Send /help for the list."


def process_updates() -> int:
    """Worker: read new messages sent to the bot and reply."""
    if not telegram.enabled():
        return 0
    offset = int(get_state("tg_offset", "0") or 0)
    params = {"limit": 100, "timeout": 0, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset + 1
    try:
        updates = telegram._call("getUpdates", **params).get("result") or []
    except Exception as exc:
        log.warning("telegram updates failed: %s", exc)
        return 0
    handled = 0
    for u in updates:
        offset = max(offset, int(u.get("update_id", 0)))
        msg = u.get("message") or {}
        chat, text = str((msg.get("chat") or {}).get("id", "")), (msg.get("text") or "").strip()
        if not chat or not text:
            continue
        try:
            reply = handle(chat, text)
        except Exception as exc:
            log.warning("telegram command failed: %s", exc)
            reply = "Sorry, that didn't work. Try again in a few minutes."
        if reply:
            telegram.send_message(chat, reply)
            handled += 1
    if updates:
        set_state("tg_offset", str(offset))
    return handled
